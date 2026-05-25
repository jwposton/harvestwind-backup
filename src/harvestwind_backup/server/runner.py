"""Server backup orchestration (Borg + cloud sync)."""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..borg import BorgManager
from ..cloud import CloudSyncManager
from ..config import ServerConfig
from ..metrics import format_bytes, format_duration, format_throughput
from ..notify.ntfy import NtfyNotifier
from ..staging_lock import staging_lock_path, wait_for_staging_unlock

logger = logging.getLogger(__name__)


@dataclass
class ServerRunner:
    config: ServerConfig
    _started: float = field(default_factory=time.monotonic)
    borg_ok: bool = False
    borg_verify_ok: bool = True
    prune_ok: bool = True
    cloud_ok: bool = False
    archive_name: str | None = None
    archives_pruned: int = 0
    bytes_synced: int = 0
    repo_stats: dict | None = None
    borg_create_seconds: float = 0.0
    borg_verify_seconds: float = 0.0
    prune_seconds: float = 0.0
    cloud_sync_seconds: float = 0.0
    cloud_verify_seconds: float = 0.0
    staging_lock_wait_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.profile_label = self._resolve_profile_label()
        self.notifier = NtfyNotifier(
            self.config.ntfy, hostname=socket.gethostname()
        )
        self.borg = BorgManager(
            Path(self.config.borg.repo_path),
            Path(self.config.borg.backup_path),
            self.config.borg.compression,
            Path(self.config.borg.cache_dir) if self.config.borg.cache_dir else None,
        )
        self.cloud = CloudSyncManager(
            self.config.b2.bucket,
            self.config.b2.path,
        )

    def _resolve_profile_label(self) -> str:
        if self.config.profile:
            return self.config.profile
        return Path(self.config.borg.backup_path).resolve().parent.name

    def _notify_title(self, title: str) -> str:
        return f"[{self.profile_label}] {title}"

    def _notify_body_prefix(self) -> str:
        return (
            f"**Profile:** `{self.profile_label}`\n"
            f"**Staging:** `{self.config.borg.backup_path}`\n"
            f"**B2:** `{self.config.b2.bucket}/{self.config.b2.path}`\n\n"
        )

    def run(self) -> bool:
        self._started = time.monotonic()
        self.notifier.notify_if(
            "success",
            self._notify_title("Server backup started"),
            self._notify_body_prefix()
            + f"Borg + cloud sync started on `{socket.gethostname()}`.",
            tags=["backup", "server", self.profile_label],
        )

        had_errors = False
        lock_ok, self.staging_lock_wait_seconds = wait_for_staging_unlock(
            self.config.borg.backup_path,
            timeout=self.config.staging_lock_wait_timeout,
            poll_interval=self.config.staging_lock_poll_interval,
        )
        if not lock_ok:
            had_errors = True
            self.notifier.notify_if(
                "failure",
                self._notify_title("Server backup aborted (client still syncing)"),
                self._notify_body_prefix()
                + (
                    f"Timed out after {format_duration(self.config.staging_lock_wait_timeout)} "
                    f"waiting for `{staging_lock_path(self.config.borg.backup_path)}`."
                ),
                tags=["backup", "server", self.profile_label],
            )
            return False

        ok, archive = self.borg.create_backup(lock_timeout=self.config.lock_timeout)
        self.borg_ok = ok
        if archive:
            self.archive_name = archive.name
            self.borg_create_seconds = archive.duration
        if not ok:
            had_errors = True
            self.notifier.notify_if(
                "failure",
                self._notify_title("Borg backup failed"),
                self._notify_body_prefix()
                + "borg create returned an error. Check server logs.",
                tags=["backup", "server", self.profile_label],
            )
        else:
            if self.config.borg.retention is not None:
                prune_ok, prune_stats = self.borg.prune_repository(
                    self.config.borg.retention,
                    lock_timeout=self.config.lock_timeout,
                )
                self.prune_ok = prune_ok
                if prune_stats:
                    self.archives_pruned = prune_stats.archives_deleted
                    self.prune_seconds = prune_stats.duration
                if not prune_ok:
                    had_errors = True
                    self.notifier.notify_if(
                        "failure",
                        self._notify_title("Borg prune failed"),
                        self._notify_body_prefix()
                        + "borg prune returned an error. Check server logs.",
                        tags=["backup", "server", self.profile_label],
                    )

            self.borg_verify_ok, self.borg_verify_seconds = self.borg.verify_repository(
                full_check=self.config.borg.full_check,
                archive_name=archive.name if archive else None,
                lock_timeout=self.config.lock_timeout,
            )
            if not self.borg_verify_ok:
                had_errors = True
                self.notifier.notify_if(
                    "failure",
                    self._notify_title("Borg verification failed"),
                    self._notify_body_prefix()
                    + "borg check returned an error. Check server logs.",
                    tags=["backup", "server", self.profile_label],
                )

            try:
                self.repo_stats = self.borg.repo_info()
            except (subprocess.CalledProcessError, OSError, ValueError) as exc:
                logger.warning("Failed to collect repository stats: %s", exc)

        if self.borg_ok and self.borg_verify_ok:
            cloud_ok, cloud_stats = self.cloud.sync(self.config.borg.repo_path)
            self.bytes_synced = cloud_stats.bytes_transferred
            self.cloud_sync_seconds = cloud_stats.duration
            if not cloud_ok:
                had_errors = True
                self.notifier.notify_if(
                    "failure",
                    self._notify_title("Cloud sync failed"),
                    self._notify_body_prefix()
                    + "rclone sync returned an error. Check server logs.",
                    tags=["backup", "server", self.profile_label],
                )
            else:
                verify_ok, verify_stats = self.cloud.verify(self.config.borg.repo_path)
                self.cloud_verify_seconds = verify_stats.duration
                if not verify_ok:
                    cloud_ok = False
                    had_errors = True
                    self.notifier.notify_if(
                        "failure",
                        self._notify_title("Cloud sync verification failed"),
                        self._notify_body_prefix()
                        + "rclone check returned an error. Check server logs.",
                        tags=["backup", "server", self.profile_label],
                    )
            self.cloud_ok = cloud_ok
        else:
            logger.warning(
                "Skipping B2 sync because Borg create or verify did not succeed"
            )
            self.cloud_ok = False

        wall = time.monotonic() - self._started
        kind = "failure" if had_errors else "success"
        self.notifier.notify_if(
            kind,
            self._notify_title(
                "Server backup complete" if not had_errors else "Server backup had errors"
            ),
            self._notify_body_prefix() + self._summary(wall, archive),
            tags=["backup", "server", kind, self.profile_label],
        )
        return not had_errors

    def _summary(self, wall: float, archive) -> str:
        backup_secs = (
            self.borg_create_seconds + self.prune_seconds + self.cloud_sync_seconds
        )
        verify_secs = self.borg_verify_seconds + self.cloud_verify_seconds
        lines = [
            f"**Duration:** {format_duration(wall)} (total)",
            f"- Backup: {format_duration(backup_secs)}",
            f"- Verify: {format_duration(verify_secs)}",
        ]
        for label, seconds in (
            ("Borg create", self.borg_create_seconds),
            ("Borg verify", self.borg_verify_seconds),
            ("Prune", self.prune_seconds),
            ("Cloud sync", self.cloud_sync_seconds),
            ("Cloud verify", self.cloud_verify_seconds),
        ):
            if seconds:
                lines.append(f"  - {label}: {format_duration(seconds)}")
        if self.staging_lock_wait_seconds:
            lines.append(
                f"  - Staging lock wait: {format_duration(self.staging_lock_wait_seconds)}"
            )
        lines.extend(
            [
                "",
                f"**Borg:** {'OK' if self.borg_ok else 'FAILED'}",
            ]
        )
        if self.borg_ok:
            lines.append(
                f"**Borg verify:** {'OK' if self.borg_verify_ok else 'FAILED'}"
            )
        if self.config.borg.retention is not None:
            prune_line = "OK" if self.prune_ok else "FAILED"
            if self.archives_pruned:
                prune_line += f" ({self.archives_pruned} archive(s) pruned)"
            lines.append(f"**Prune:** {prune_line}")
        if self.borg_ok and self.borg_verify_ok:
            lines.append(f"**Cloud:** {'OK' if self.cloud_ok else 'FAILED'}")
        else:
            lines.append("**Cloud:** SKIPPED (Borg failed)")
        if archive:
            lines.extend(
                [
                    "",
                    f"**Archive:** `{archive.name}`",
                    f"- Original: {format_bytes(archive.size_orig)}",
                    f"- Deduplicated: {format_bytes(archive.size_deduplicated)}",
                    f"- Files: {archive.num_files}",
                ]
            )
        if self.repo_stats:
            lines.extend(
                [
                    "",
                    "**Repository**",
                    f"- Archives: {self.repo_stats.get('total_archives', 0)}",
                    f"- Unique size: {format_bytes(self.repo_stats.get('total_size', 0))}",
                ]
            )
        if self.bytes_synced:
            lines.append(
                f"- Cloud transferred: {format_bytes(self.bytes_synced)} "
                f"({format_throughput(self.bytes_synced / max(wall, 1))})"
            )
        return "\n".join(lines)
