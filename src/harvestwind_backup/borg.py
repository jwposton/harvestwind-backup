"""Borg archive creation and repository info."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import BorgRetention

logger = logging.getLogger(__name__)


@dataclass
class BorgArchiveStats:
    name: str
    size_orig: int
    size_compressed: int
    size_deduplicated: int
    num_files: int
    duration: float


@dataclass
class BorgPruneStats:
    archives_deleted: int = 0
    duration: float = 0.0


class BorgManager:
    def __init__(
        self,
        repo_path: Path,
        backup_path: Path,
        compression: str,
        cache_dir: Path | None = None,
    ):
        self.repo_path = repo_path
        self.backup_path = backup_path
        self.compression = compression
        if cache_dir:
            os.environ["BORG_CACHE_DIR"] = str(cache_dir)
        subprocess.run(["borg", "--version"], check=True, capture_output=True)

    def create_backup(self, lock_timeout: int = 300) -> tuple[bool, BorgArchiveStats | None]:
        if not self.backup_path.exists():
            logger.error("Backup path not found: %s", self.backup_path)
            return False, None

        archive_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        cmd = [
            "borg",
            "create",
            f"--compression={self.compression}",
            f"--lock-wait={lock_timeout}",
            "--numeric-ids",
            "--stats",
            f"{self.repo_path}::{archive_name}",
            str(self.backup_path),
        ]
        start = datetime.now()
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = (datetime.now() - start).total_seconds()

        if result.returncode != 0:
            logger.error("borg create failed: %s", result.stderr)
            return False, None

        stats = self._parse_stats(result.stderr + result.stdout, archive_name, duration)
        return True, stats

    def list_archives(self) -> list[str]:
        result = subprocess.run(
            [
                "borg",
                "list",
                "--format",
                "{name}{NL}",
                str(self.repo_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("borg list failed: %s", result.stderr)
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def verify_repository(
        self,
        *,
        full_check: bool = False,
        archive_name: str | None = None,
        lock_timeout: int = 300,
    ) -> tuple[bool, float]:
        if full_check:
            cmd = ["borg", "check", f"--lock-wait={lock_timeout}", str(self.repo_path)]
            label = "full repository"
        else:
            name = archive_name
            if not name:
                archives = self.list_archives()
                if not archives:
                    logger.error("borg verify: no archives in repository")
                    return False, 0.0
                name = archives[-1]
            cmd = [
                "borg",
                "check",
                f"--lock-wait={lock_timeout}",
                f"{self.repo_path}::{name}",
            ]
            label = f"archive {name}"
        logger.info("borg check starting (%s)", label)
        start = datetime.now()
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = (datetime.now() - start).total_seconds()
        if result.returncode != 0:
            logger.error("borg check failed (%s): %s", label, result.stderr)
            return False, duration
        logger.info("borg check passed (%s) in %.1fs", label, duration)
        return True, duration

    def prune_argv(self, retention: BorgRetention, lock_timeout: int = 300) -> list[str]:
        cmd = ["borg", "prune", f"--lock-wait={lock_timeout}", "--stats"]
        for flag, count in (
            ("--keep-daily", retention.daily),
            ("--keep-weekly", retention.weekly),
            ("--keep-monthly", retention.monthly),
            ("--keep-yearly", retention.yearly),
        ):
            if count > 0:
                cmd.append(f"{flag}={count}")
        cmd.append(str(self.repo_path))
        return cmd

    def prune_repository(
        self, retention: BorgRetention, lock_timeout: int = 300
    ) -> tuple[bool, BorgPruneStats | None]:
        cmd = self.prune_argv(retention, lock_timeout)
        start = datetime.now()
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = (datetime.now() - start).total_seconds()
        combined = (result.stdout or "") + (result.stderr or "")

        if result.returncode != 0:
            logger.error("borg prune failed: %s", result.stderr)
            return False, None

        pruning = re.findall(r"Pruning archive\b", combined, flags=re.IGNORECASE)
        stats = BorgPruneStats(
            archives_deleted=len(pruning),
            duration=duration,
        )
        logger.info(
            "borg prune finished in %.1fs (%d archive(s) pruned)",
            duration,
            stats.archives_deleted,
        )
        return True, stats

    def _parse_stats(
        self, output: str, archive_name: str, duration: float
    ) -> BorgArchiveStats:
        def grab(pattern: str) -> int:
            match = re.search(pattern, output)
            if not match:
                return 0
            return int(match.group(1).replace(",", ""))

        return BorgArchiveStats(
            name=archive_name,
            size_orig=grab(r"Original size\s+(\d+)"),
            size_compressed=grab(r"Compressed size\s+(\d+)"),
            size_deduplicated=grab(r"Deduplicated size\s+(\d+)"),
            num_files=grab(r"Number of files\s+(\d+)"),
            duration=duration,
        )

    def repo_info(self) -> dict:
        result = subprocess.run(
            ["borg", "info", "--json", str(self.repo_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        cache = data.get("cache", {})
        return {
            "total_archives": data.get("archives", {}).get("count", 0),
            "total_size": cache.get("stats", {}).get("unique", {}).get("total_size", 0),
        }
