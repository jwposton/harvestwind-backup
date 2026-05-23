"""Rsync backup with reliable --stats parsing."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metrics import RsyncStats, parse_rsync_stats

logger = logging.getLogger(__name__)

# Itemized rsync output: file would be transferred on a real run
_RSYNC_CHANGE = re.compile(r"^[<>ch.*]")

@dataclass
class RsyncOptions:
    compress: bool = False
    delete: bool = True
    partial: bool = True
    checksum: bool = False
    whole_file: bool = False
    copy_unsafe_links: bool = True
    bwlimit: int | None = None
    timeout: int = 1800
    exclude: list[str] = field(
        default_factory=lambda: [".git/", "*.tmp", "*.temp", "*~"]
    )
    include: list[str] = field(default_factory=list)


@dataclass
class RsyncVerifyConfig:
    """Post-sync rsync dry-run. Defaults match the sync pass (mtime+size, not checksum)."""

    enabled: bool = True
    checksum: bool | None = None
    skip_if_unchanged: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RsyncVerifyConfig":
        if not data:
            return cls()
        checksum = data.get("checksum")
        return cls(
            enabled=bool(data.get("enabled", True)),
            checksum=bool(checksum) if checksum is not None else None,
            skip_if_unchanged=bool(data.get("skip_if_unchanged", True)),
        )


class RsyncError(Exception):
    pass


class RsyncManager:
    def __init__(self, config: dict[str, Any]):
        self.server_destination = config.get("server_destination", {})
        self.additional_destinations = config.get("additional_destinations", [])
        self.verify_config = RsyncVerifyConfig.from_dict(config.get("verify"))
        self.default_options = RsyncOptions(
            compress=config.get("compress", False),
            delete=config.get("delete", True),
            checksum=config.get("checksum", False),
            whole_file=config.get("whole_file", False),
            copy_unsafe_links=config.get("copy_unsafe_links", True),
            bwlimit=config.get("bwlimit"),
            timeout=config.get("timeout", 1800),
            exclude=config.get("exclude") or RsyncOptions().exclude,
            include=config.get("include") or [],
        )

    def _build_command(
        self, source: str, dest: str, options: RsyncOptions
    ) -> list[str]:
        cmd = ["rsync", "-a", "--stats", "--info=stats2"]
        if options.compress:
            cmd.append("-z")
        if options.delete:
            cmd.append("--delete")
        if options.partial:
            cmd.append("--partial")
        if options.checksum:
            cmd.append("--checksum")
        if options.whole_file:
            cmd.append("-W")
        if options.copy_unsafe_links:
            cmd.append("--copy-unsafe-links")
        if options.bwlimit:
            cmd.extend(["--bwlimit", str(options.bwlimit)])
        cmd.extend(["--timeout", str(options.timeout)])
        for pattern in options.include:
            cmd.append(f"--include={pattern}")
        for pattern in options.exclude:
            cmd.append(f"--exclude={pattern}")
        cmd.extend([source.rstrip("/") + "/", dest])
        return cmd

    def _ssh_wrapper(self, dest: dict[str, Any]) -> list[str]:
        ssh_opts = [
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-i",
            dest["auth"]["key_path"],
        ]
        if dest.get("port", 22) != 22:
            ssh_opts.extend(["-p", str(dest["port"])])
        return ["-e", "ssh " + " ".join(ssh_opts)]

    def sync(
        self, source_path: str, dest: dict[str, Any], app_name: str
    ) -> tuple[bool, RsyncStats | None]:
        if not os.path.isdir(source_path):
            raise RsyncError(f"Source path does not exist: {source_path}")

        options_dict = dest.get("options") or {}
        options = RsyncOptions(
            **{**self.default_options.__dict__, **options_dict}
        )

        if dest.get("type") == "ssh":
            destination = f"{dest['user']}@{dest['host']}:{dest['remote_path']}"
        else:
            destination = dest["path"]

        cmd = self._build_command(source_path, destination, options)
        if dest.get("type") == "ssh":
            cmd[1:1] = self._ssh_wrapper(dest)

        logger.info("Starting rsync for %s -> %s", app_name, destination)
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.SubprocessError as exc:
            raise RsyncError(str(exc)) from exc

        wall = time.monotonic() - start
        combined = (result.stdout or "") + "\n" + (result.stderr or "")

        if result.returncode != 0:
            raise RsyncError(
                f"rsync failed (rc={result.returncode}): {result.stderr.strip()}"
            )

        stats = parse_rsync_stats(combined, wall_seconds=wall)
        stats.duration = wall
        logger.info(
            "Rsync %s: %s files, %s bytes in %s",
            app_name,
            stats.files_transferred,
            stats.bytes_transferred,
            f"{wall:.1f}s",
        )
        return True, stats

    def backup_app(
        self, app_name: str, source_path: str
    ) -> tuple[bool, RsyncStats | None, float, float]:
        server_dest = self._server_dest_for_app(source_path)

        ok, stats = self.sync(source_path, server_dest, app_name)
        backup_secs = stats.duration if stats else 0.0
        if not ok:
            return False, None, backup_secs, 0.0

        source_dir = Path(source_path).name
        for dest in self.additional_destinations:
            extra = dict(dest)
            if extra.get("type") == "ssh":
                extra["remote_path"] = os.path.join(
                    extra["remote_path"], source_dir
                )
            else:
                extra["path"] = os.path.join(extra.get("path", ""), source_dir)
            try:
                self.sync(source_path, extra, app_name)
            except RsyncError as exc:
                logger.error("Additional destination failed: %s", exc)
                ok = False

        verify_secs = 0.0
        if ok:
            verify_cfg = self._verify_config_for(server_dest)
            if not verify_cfg.enabled:
                logger.info("Rsync verify disabled for %s", app_name)
            elif (
                verify_cfg.skip_if_unchanged
                and stats
                and stats.files_transferred == 0
            ):
                logger.info(
                    "Skipping rsync verify for %s (no files transferred)",
                    app_name,
                )
            else:
                ok, verify_secs = self.verify_app(
                    app_name, source_path, server_dest, verify_cfg=verify_cfg
                )
                if not ok:
                    return False, stats, backup_secs, verify_secs

        return ok, stats, backup_secs, verify_secs

    def _server_dest_for_app(self, source_path: str) -> dict[str, Any]:
        source_dir = Path(source_path).name
        if self.server_destination.get("type") == "ssh":
            return {
                "type": "ssh",
                "host": self.server_destination["host"],
                "user": self.server_destination["user"],
                "remote_path": os.path.join(
                    self.server_destination["remote_path"], source_dir
                ),
                "port": self.server_destination.get("port", 22),
                "auth": self.server_destination["auth"],
                "options": self.server_destination.get("options", {}),
            }
        return {
            "path": os.path.join(self.server_destination["path"], source_dir),
            "options": self.server_destination.get("options", {}),
        }

    def _path_matches_exclude(self, path: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            pat = pattern.rstrip("/")
            if path == pat or path.startswith(f"{pat}/"):
                return True
            if "*" in pat:
                regex = "^" + re.escape(pat).replace(r"\*", "[^/]*") + "(/|$)"
                if re.match(regex, path):
                    return True
        return False

    def _verify_config_for(self, dest: dict[str, Any]) -> RsyncVerifyConfig:
        dest_verify = dest.get("verify") or (dest.get("options") or {}).get("verify")
        if not dest_verify:
            return self.verify_config
        merged: dict[str, Any] = {
            "enabled": self.verify_config.enabled,
            "skip_if_unchanged": self.verify_config.skip_if_unchanged,
        }
        if self.verify_config.checksum is not None:
            merged["checksum"] = self.verify_config.checksum
        merged.update(dest_verify)
        return RsyncVerifyConfig.from_dict(merged)

    def _verify_checksum(
        self, verify_cfg: RsyncVerifyConfig, sync_options: RsyncOptions
    ) -> bool:
        if verify_cfg.checksum is not None:
            return verify_cfg.checksum
        return sync_options.checksum

    def verify_app(
        self,
        app_name: str,
        source_path: str,
        dest: dict[str, Any] | None = None,
        *,
        verify_cfg: RsyncVerifyConfig | None = None,
    ) -> tuple[bool, float]:
        """Dry-run compare: local tree must match remote copy.

        Uses the same comparison mode as sync (mtime+size by default). Full
        checksum verify is opt-in via ``rsync.verify.checksum: true``.
        """
        dest = dest or self._server_dest_for_app(source_path)
        verify_cfg = verify_cfg or self._verify_config_for(dest)
        options_dict = dest.get("options") or {}
        options = RsyncOptions(
            **{**self.default_options.__dict__, **options_dict}
        )
        verify_checksum = self._verify_checksum(verify_cfg, options)
        verify_options = RsyncOptions(
            **{**options.__dict__, "checksum": verify_checksum}
        )

        if dest.get("type") == "ssh":
            destination = f"{dest['user']}@{dest['host']}:{dest['remote_path']}"
        else:
            destination = dest["path"]

        cmd = self._build_command(source_path, destination, verify_options)
        if dest.get("type") == "ssh":
            cmd[1:1] = self._ssh_wrapper(dest)
        cmd.extend(["--dry-run", "-i"])

        mode = "checksum" if verify_checksum else "mtime+size"
        logger.info(
            "Verifying rsync for %s -> %s (%s)",
            app_name,
            destination,
            mode,
        )
        start = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        duration = time.monotonic() - start
        if result.returncode != 0:
            logger.error(
                "Rsync verify failed for %s (rc=%s): %s",
                app_name,
                result.returncode,
                result.stderr.strip(),
            )
            return False, duration

        changes: list[str] = []
        for line in (result.stdout or "").splitlines():
            if not line or line.startswith(("sending", "total size")):
                continue
            if not _RSYNC_CHANGE.match(line):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            rel = parts[1].lstrip("./")
            if self._path_matches_exclude(rel, verify_options.exclude):
                continue
            changes.append(rel)

        if changes:
            logger.error(
                "Rsync verify failed for %s: %d path(s) differ (%s…)",
                app_name,
                len(changes),
                ", ".join(changes[:5]),
            )
            return False, duration

        logger.info("Rsync verify passed for %s in %.1fs", app_name, duration)
        return True, duration
