"""Client backup orchestration."""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ClientConfig
from ..discovery import VolumeDiscovery, discover_apps
from ..metrics import TransferTotals, format_bytes, format_duration, format_throughput
from ..notify.ntfy import NtfyNotifier
from ..rsync import RsyncError, RsyncManager
from ..volumes import VolumeManager

logger = logging.getLogger(__name__)


@dataclass
class ProcessStats:
    apps_ok: int = 0
    apps_failed: int = 0
    volumes_ok: int = 0
    volumes_failed: int = 0
    rsync_ok: int = 0
    rsync_failed: int = 0
    rsync_skipped: int = 0
    volumes_verify_failed: int = 0
    rsync_verify_failed: int = 0
    backup_seconds: float = 0.0
    verify_seconds: float = 0.0


@dataclass
class ClientRunner:
    config: ClientConfig
    log_dir: Path = Path("/var/log/harvestwind-backup")

    stats: ProcessStats = field(default_factory=ProcessStats)
    transfers: TransferTotals = field(default_factory=TransferTotals)
    _started: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.notifier = NtfyNotifier(
            self.config.ntfy, hostname=socket.gethostname()
        )
        self.rsync = RsyncManager(self.config.rsync)
        self.volumes = VolumeManager(
            max_backups=self.config.volumes.max_backups,
            uid=self.config.volumes.uid,
            gid=self.config.volumes.gid,
        )
        self.volume_discovery = VolumeDiscovery()

    def run(self) -> bool:
        self._started = time.monotonic()
        self.notifier.notify_if(
            "success",
            "Backup started",
            f"Client backup started on `{socket.gethostname()}`.",
            tags=["backup", "client"],
        )

        apps = discover_apps(Path(self.config.apps_root))
        had_errors = False

        for app in apps:
            if not self._backup_app(app):
                had_errors = True

        wall = time.monotonic() - self._started
        body = self._summary_markdown(wall, had_errors)
        kind = "failure" if had_errors else "success"
        self.notifier.notify_if(
            kind,
            "Backup complete" if not had_errors else "Backup finished with errors",
            body,
            tags=["backup", "client", kind],
        )
        return not had_errors

    def _backup_app(self, app) -> bool:
        compose = app.compose_file
        was_running = self._stack_running(compose)
        ok = True

        try:
            if was_running:
                subprocess.run(
                    ["docker", "compose", "-f", str(compose), "down"],
                    check=False,
                    capture_output=True,
                )

            backup_dir = app.path / self.config.volumes.backup_dir
            for volume in self.volume_discovery.get_volumes(compose):
                result = self.volumes.backup_volume(volume, backup_dir)
                self.stats.backup_seconds += result.get("backup_duration", 0.0)
                self.stats.verify_seconds += result.get("verify_duration", 0.0)
                if result.get("success"):
                    self.stats.volumes_ok += 1
                elif result.get("verified") is False:
                    self.stats.volumes_failed += 1
                    self.stats.volumes_verify_failed += 1
                    ok = False
                else:
                    self.stats.volumes_failed += 1
                    ok = False

            try:
                rsync_ok, rsync_stats, rsync_backup_secs, rsync_verify_secs = (
                    self.rsync.backup_app(app.name, str(app.path))
                )
                self.stats.backup_seconds += rsync_backup_secs
                self.stats.verify_seconds += rsync_verify_secs
                if rsync_ok and rsync_stats:
                    self.stats.rsync_ok += 1
                    self.transfers.add(rsync_stats)
                elif rsync_ok:
                    self.stats.rsync_ok += 1
                else:
                    self.stats.rsync_failed += 1
                    self.stats.rsync_verify_failed += 1
                    ok = False
            except RsyncError as exc:
                logger.error("Rsync failed for %s: %s", app.name, exc)
                self.stats.rsync_failed += 1
                ok = False
                self.notifier.notify_if(
                    "failure",
                    f"Rsync failed: {app.name}",
                    str(exc),
                    tags=["backup", "client"],
                )

            if was_running:
                subprocess.run(
                    ["docker", "compose", "-f", str(compose), "up", "-d"],
                    check=True,
                )

        except Exception as exc:
            logger.exception("App backup failed for %s", app.name)
            ok = False
            if was_running:
                subprocess.run(
                    ["docker", "compose", "-f", str(compose), "up", "-d"],
                    check=False,
                )

        if ok:
            self.stats.apps_ok += 1
        else:
            self.stats.apps_failed += 1
        return ok

    def _stack_running(self, compose_file: Path) -> bool:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "ps", "-q"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())

    def _summary_markdown(self, wall_seconds: float, had_errors: bool) -> str:
        status = "Failed" if had_errors else "Success"
        lines = [
            f"**Status:** {status}",
            f"**Duration:** {format_duration(wall_seconds)} (total)",
            f"- Backup: {format_duration(self.stats.backup_seconds)}",
            f"- Verify: {format_duration(self.stats.verify_seconds)}",
            "",
            "**Counts**",
            f"- Apps OK: {self.stats.apps_ok} / failed: {self.stats.apps_failed}",
            f"- Volumes OK: {self.stats.volumes_ok} / failed: {self.stats.volumes_failed}",
            f"- Rsync OK: {self.stats.rsync_ok} / failed: {self.stats.rsync_failed}",
        ]
        if self.stats.volumes_verify_failed:
            lines.append(
                f"- Volume verify failed: {self.stats.volumes_verify_failed}"
            )
        if self.stats.rsync_verify_failed:
            lines.append(f"- Rsync verify failed: {self.stats.rsync_verify_failed}")
        if self.transfers.bytes > 0:
            lines.extend(
                [
                    "",
                    "**Transfer**",
                    f"- Files: {self.transfers.files}",
                    f"- Data: {format_bytes(self.transfers.bytes)}",
                    f"- Throughput: {format_throughput(self.transfers.throughput(wall_seconds))}",
                ]
            )
        return "\n".join(lines)
