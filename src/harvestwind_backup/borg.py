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

logger = logging.getLogger(__name__)


@dataclass
class BorgArchiveStats:
    name: str
    size_orig: int
    size_compressed: int
    size_deduplicated: int
    num_files: int
    duration: float


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
