"""Server backup orchestration (Borg + cloud sync)."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..borg import BorgManager
from ..cloud import CloudSyncManager
from ..config import ServerConfig
from ..metrics import format_bytes, format_duration, format_throughput
from ..notify.ntfy import NtfyNotifier

logger = logging.getLogger(__name__)


@dataclass
class ServerRunner:
    config: ServerConfig
    _started: float = field(default_factory=time.monotonic)
    borg_ok: bool = False
    prune_ok: bool = True
    cloud_ok: bool = False
    archive_name: str | None = None
    archives_pruned: int = 0
    bytes_synced: int = 0

    def __post_init__(self) -> None:
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

    def run(self) -> bool:
        self._started = time.monotonic()
        self.notifier.notify_if(
            "success",
            "Server backup started",
            f"Borg + cloud sync started on `{socket.gethostname()}`.",
            tags=["backup", "server"],
        )

        had_errors = False
        ok, archive = self.borg.create_backup(lock_timeout=self.config.lock_timeout)
        self.borg_ok = ok
        if archive:
            self.archive_name = archive.name
        if not ok:
            had_errors = True
            self.notifier.notify_if(
                "failure",
                "Borg backup failed",
                "borg create returned an error. Check server logs.",
                tags=["backup", "server"],
            )
        elif self.config.borg.retention is not None:
            prune_ok, prune_stats = self.borg.prune_repository(
                self.config.borg.retention,
                lock_timeout=self.config.lock_timeout,
            )
            self.prune_ok = prune_ok
            if prune_stats:
                self.archives_pruned = prune_stats.archives_deleted
            if not prune_ok:
                had_errors = True
                self.notifier.notify_if(
                    "failure",
                    "Borg prune failed",
                    "borg prune returned an error. Check server logs.",
                    tags=["backup", "server"],
                )

        cloud_ok, cloud_stats = self.cloud.sync(self.config.borg.repo_path)
        self.bytes_synced = cloud_stats.bytes_transferred
        if not cloud_ok:
            had_errors = True
            self.notifier.notify_if(
                "failure",
                "Cloud sync failed",
                "rclone sync returned an error. Check server logs.",
                tags=["backup", "server"],
            )
        elif not self.cloud.verify(self.config.borg.repo_path)[0]:
            cloud_ok = False
            had_errors = True
            self.notifier.notify_if(
                "failure",
                "Cloud sync verification failed",
                "rclone check returned an error. Check server logs.",
                tags=["backup", "server"],
            )
        self.cloud_ok = cloud_ok

        wall = time.monotonic() - self._started
        kind = "failure" if had_errors else "success"
        self.notifier.notify_if(
            kind,
            "Server backup complete" if not had_errors else "Server backup had errors",
            self._summary(wall, archive),
            tags=["backup", "server", kind],
        )
        return not had_errors

    def _summary(self, wall: float, archive) -> str:
        lines = [
            f"**Duration:** {format_duration(wall)}",
            f"**Borg:** {'OK' if self.borg_ok else 'FAILED'}",
        ]
        if self.config.borg.retention is not None:
            prune_line = "OK" if self.prune_ok else "FAILED"
            if self.archives_pruned:
                prune_line += f" ({self.archives_pruned} archive(s) pruned)"
            lines.append(f"**Prune:** {prune_line}")
        lines.append(f"**Cloud:** {'OK' if self.cloud_ok else 'FAILED'}")
        if archive:
            lines.extend(
                [
                    "",
                    f"**Archive:** `{archive.name}`",
                    f"- Original: {format_bytes(archive.size_orig)}",
                    f"- Deduplicated: {format_bytes(archive.size_deduplicated)}",
                    f"- Files: {archive.num_files}",
                    f"- Borg duration: {archive.duration:.1f}s",
                ]
            )
        if self.bytes_synced:
            lines.append(
                f"- Cloud transferred: {format_bytes(self.bytes_synced)} "
                f"({format_throughput(self.bytes_synced / max(wall, 1))})"
            )
        return "\n".join(lines)
