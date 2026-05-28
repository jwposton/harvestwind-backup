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
from .metrics import parse_byte_size

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


_RE_PRUNED_ARCHIVE = re.compile(
    r"^\s*Pruning(?: archive)?:\s+\S",
    re.IGNORECASE | re.MULTILINE,
)


def parse_prune_deleted_count(output: str) -> int:
    """Count archives removed by ``borg prune --list`` (not dry-run ``Would prune``)."""
    return len(_RE_PRUNED_ARCHIVE.findall(output))


_RE_THIS_ARCHIVE_ROW = re.compile(
    r"This archive:\s+"
    r"([\d,]+(?:\.\d+)?\s*[KMGT]?i?B)\s+"
    r"([\d,]+(?:\.\d+)?\s*[KMGT]?i?B)\s+"
    r"([\d,]+(?:\.\d+)?\s*[KMGT]?i?B)",
    re.IGNORECASE,
)
def _parse_size_token(token: str) -> int:
    parts = token.strip().split(maxsplit=1)
    if len(parts) == 2:
        return parse_byte_size(parts[0], parts[1])
    return parse_byte_size(parts[0])


def _grab_stat_label(output: str, label: str) -> int:
    for pattern in (
        rf"{re.escape(label)}:\s*([\d,]+(?:\.\d+)?\s*[KMGT]?i?B)",
        rf"{re.escape(label)}\s+([\d,]+(?:\.\d+)?\s*[KMGT]?i?B)",
        rf"{re.escape(label)}:\s*([\d,]+)",
        rf"{re.escape(label)}\s+(\d+)",
    ):
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
            if re.search(r"[KMGT]i?B", value, re.IGNORECASE):
                return _parse_size_token(value)
            return int(value.replace(",", ""))
    return 0


def parse_create_stats(output: str, archive_name: str, duration: float) -> BorgArchiveStats:
    """Parse ``borg create --stats`` (human-readable or plain-byte output)."""
    row = _RE_THIS_ARCHIVE_ROW.search(output)
    if row:
        return BorgArchiveStats(
            name=archive_name,
            size_orig=_parse_size_token(row.group(1)),
            size_compressed=_parse_size_token(row.group(2)),
            size_deduplicated=_parse_size_token(row.group(3)),
            num_files=_grab_stat_label(output, "Number of files"),
            duration=duration,
        )
    return BorgArchiveStats(
        name=archive_name,
        size_orig=_grab_stat_label(output, "Original size"),
        size_compressed=_grab_stat_label(output, "Compressed size"),
        size_deduplicated=_grab_stat_label(output, "Deduplicated size"),
        num_files=_grab_stat_label(output, "Number of files"),
        duration=duration,
    )


def parse_repo_info_json(data: dict) -> dict[str, int]:
    """Parse ``borg info --json`` for archive count and unique repo size."""
    total_archives = 0
    archives = data.get("archives")
    if isinstance(archives, list):
        total_archives = len(archives)
    elif isinstance(archives, dict):
        total_archives = int(archives.get("count", 0) or 0)
    if total_archives == 0:
        repository = data.get("repository") or {}
        for key in ("archive_count", "archives_count", "count"):
            value = repository.get(key)
            if value is not None:
                total_archives = int(value)
                break

    cache = data.get("cache") or {}
    stats = cache.get("stats") or {}
    total_size = (
        stats.get("unique_size")
        or stats.get("total_unique_size")
        or (stats.get("unique") or {}).get("total_size")
        or stats.get("total_size")
        or 0
    )

    return {
        "total_archives": total_archives,
        "total_size": int(total_size or 0),
    }


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

        stats = parse_create_stats(result.stderr + result.stdout, archive_name, duration)
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
        cmd = ["borg", "prune", f"--lock-wait={lock_timeout}", "--stats", "--list"]
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

        archives_deleted = parse_prune_deleted_count(combined)
        stats = BorgPruneStats(
            archives_deleted=archives_deleted,
            duration=duration,
        )
        logger.info(
            "borg prune finished in %.1fs (%d archive(s) pruned)",
            duration,
            stats.archives_deleted,
        )
        return True, stats

    def repo_info(self) -> dict[str, int]:
        result = subprocess.run(
            ["borg", "info", "--json", str(self.repo_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        info = parse_repo_info_json(json.loads(result.stdout))
        if info["total_archives"] == 0:
            archives = self.list_archives()
            if archives:
                info["total_archives"] = len(archives)
        return info
