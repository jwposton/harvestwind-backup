"""B2 sync via rclone."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .metrics import format_bytes, format_throughput, parse_byte_size

logger = logging.getLogger(__name__)

# Match legacy unified_backup CloudSyncManager sync_to_cloud behavior.
_RCLONE_SYNC_FLAGS = [
    "--create-empty-src-dirs",
    "--track-renames",
    "--delete-after",
]

_RCLONE_RESILIENCE_FLAGS = [
    "--transfers",
    "4",
    "--checkers",
    "8",
    "--retries",
    "3",
    "--low-level-retries",
    "10",
]

_RE_RCLONE = re.compile(
    r"Transferred:\s+([\d.]+\s*[KMGT]?i?B)\s*/\s*([\d.]+\s*[KMGT]?i?B)",
    re.IGNORECASE,
)


@dataclass
class CloudSyncStats:
    bytes_transferred: int = 0
    duration: float = 0.0
    bytes_per_sec: float = 0.0


class CloudSyncManager:
    def __init__(
        self,
        bucket: str,
        remote_path: str,
        *,
        bwlimit: str | None = "10M",
        max_retries: int = 3,
    ):
        self.remote = f"b2:{bucket}/{remote_path.strip('/')}"
        self.bwlimit = bwlimit
        self.max_retries = max_retries

    def _run_with_retry(self, base_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run rclone with resilience flags; retry with exponential backoff (legacy)."""
        last: subprocess.CompletedProcess[str] | None = None
        for attempt in range(self.max_retries + 1):
            cmd = [*base_cmd, *_RCLONE_RESILIENCE_FLAGS]
            if self.bwlimit:
                cmd.extend(["--bwlimit", self.bwlimit])

            last = subprocess.run(cmd, capture_output=True, text=True)
            if last.returncode == 0:
                return last

            if attempt < self.max_retries:
                wait = 2**attempt
                logger.warning(
                    "rclone failed (exit %s), retrying in %ss (%s/%s): %s",
                    last.returncode,
                    wait,
                    attempt + 1,
                    self.max_retries,
                    (last.stderr or "").strip()[:500],
                )
                time.sleep(wait)

        assert last is not None
        return last

    def _parse_transfer_stats(
        self, result: subprocess.CompletedProcess[str], duration: float
    ) -> CloudSyncStats:
        stats = CloudSyncStats(duration=duration)
        combined = (result.stdout or "") + (result.stderr or "")
        for line in combined.splitlines():
            match = _RE_RCLONE.search(line)
            if match:
                try:
                    stats.bytes_transferred = parse_byte_size(
                        match.group(1).split()[0],
                        match.group(1).split()[1] if " " in match.group(1) else "B",
                    )
                except ValueError:
                    pass
        if stats.bytes_transferred and duration > 0:
            stats.bytes_per_sec = stats.bytes_transferred / duration
        return stats

    def sync(self, local_path: str) -> tuple[bool, CloudSyncStats]:
        path = Path(local_path)
        if not path.exists():
            logger.error("Local path does not exist: %s", local_path)
            return False, CloudSyncStats()

        cmd = [
            "rclone",
            "sync",
            str(path),
            self.remote,
            *_RCLONE_SYNC_FLAGS,
            "--stats",
            "5s",
            "--stats-one-line",
        ]
        logger.info("Starting sync to B2: %s -> %s", path, self.remote)
        start = time.monotonic()
        result = self._run_with_retry(cmd)
        duration = time.monotonic() - start
        stats = self._parse_transfer_stats(result, duration)

        if result.returncode != 0:
            logger.error("rclone sync failed: %s", result.stderr)
            return False, stats

        logger.info(
            "Cloud sync: %s in %s (%s)",
            format_bytes(stats.bytes_transferred),
            f"{duration:.1f}s",
            format_throughput(stats.bytes_per_sec),
        )
        return True, stats

    def verify(self, local_path: str) -> tuple[bool, CloudSyncStats]:
        """One-way check: every local file exists on remote with matching hash (legacy)."""
        path = Path(local_path)
        if not path.exists():
            logger.error("Local path does not exist: %s", local_path)
            return False, CloudSyncStats()

        cmd = [
            "rclone",
            "check",
            str(path),
            self.remote,
            "--one-way",
        ]
        logger.info("Verifying B2 sync: %s <-> %s", path, self.remote)
        start = time.monotonic()
        result = self._run_with_retry(cmd)
        duration = time.monotonic() - start

        if result.returncode != 0:
            logger.error("rclone check failed: %s", result.stderr)
            return False, CloudSyncStats(duration=duration)

        logger.info("Cloud sync verification passed in %.1fs", duration)
        return True, CloudSyncStats(duration=duration)
