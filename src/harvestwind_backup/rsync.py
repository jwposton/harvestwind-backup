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


class RsyncError(Exception):
    pass


class RsyncManager:
    def __init__(self, config: dict[str, Any]):
        self.server_destination = config.get("server_destination", {})
        self.additional_destinations = config.get("additional_destinations", [])
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
    ) -> tuple[bool, RsyncStats | None]:
        server_dest = self._server_dest_for_app(source_path)

        ok, stats = self.sync(source_path, server_dest, app_name)
        if not ok:
            return False, None

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

        if ok and not self.verify_app(app_name, source_path, server_dest):
            return False, stats

        return ok, stats

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

    def verify_app(
        self, app_name: str, source_path: str, dest: dict[str, Any] | None = None
    ) -> bool:
        """Dry-run checksum compare: local tree must match remote copy (legacy-style)."""
        dest = dest or self._server_dest_for_app(source_path)
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
        cmd.extend(["--dry-run", "--checksum", "-i"])

        logger.info("Verifying rsync for %s -> %s", app_name, destination)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(
                "Rsync verify failed for %s (rc=%s): %s",
                app_name,
                result.returncode,
                result.stderr.strip(),
            )
            return False

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
            if self._path_matches_exclude(rel, options.exclude):
                continue
            changes.append(rel)

        if changes:
            logger.error(
                "Rsync verify failed for %s: %d path(s) differ (%s…)",
                app_name,
                len(changes),
                ", ".join(changes[:5]),
            )
            return False

        logger.info("Rsync verify passed for %s", app_name)
        return True
