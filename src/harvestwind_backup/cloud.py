"""B2 sync via rclone."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass

from .metrics import format_bytes, format_throughput, parse_byte_size

logger = logging.getLogger(__name__)

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
    def __init__(self, bucket: str, remote_path: str, *, bwlimit: str | None = "10M"):
        self.remote = f"b2:{bucket}/{remote_path.strip('/')}"
        self.bwlimit = bwlimit

    def sync(self, local_path: str) -> tuple[bool, CloudSyncStats]:
        cmd = [
            "rclone",
            "sync",
            local_path,
            self.remote,
            "--stats",
            "5s",
            "--stats-one-line",
        ]
        if self.bwlimit:
            cmd.extend(["--bwlimit", self.bwlimit])

        start = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = time.monotonic() - start
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
