"""Parse and format backup metrics (bytes, duration, throughput)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIZE_SUFFIX = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
}

_RE_SIZE = re.compile(
    r"^([\d,]+(?:\.\d+)?)\s*([KMGT]?B?)?$",
    re.IGNORECASE,
)
_RE_RSYNC_FILES = re.compile(r"Number of (?:regular )?files transferred:\s*([\d,]+)")
_RE_RSYNC_TRANSFERRED = re.compile(
    r"Total transferred file size:\s*([\d,]+(?:\.\d+)?)\s*([KMGT]?B?)?",
    re.IGNORECASE,
)
_RE_RSYNC_TOTAL_FILE = re.compile(
    r"Total file size:\s*([\d,]+(?:\.\d+)?)\s*([KMGT]?B?)?",
    re.IGNORECASE,
)
_RE_RSYNC_SPEED = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*bytes/sec",
    re.IGNORECASE,
)


def parse_byte_size(value: str, unit: str = "") -> int:
    """Parse a size token (with optional unit) into bytes."""
    cleaned = value.replace(",", "").strip()
    suffix = (unit or "").upper().strip()
    if suffix and not suffix.endswith("B"):
        suffix = f"{suffix}B"
    match = _RE_SIZE.match(f"{cleaned} {suffix}".strip() if suffix else cleaned)
    if not match:
        raise ValueError(f"Cannot parse size: {value!r} {unit!r}")
    number = float(match.group(1))
    mult = _SIZE_SUFFIX.get(match.group(2).upper() if match.group(2) else "B", 1)
    return int(number * mult)


def format_bytes(num_bytes: int | float) -> str:
    """Format byte count using binary units."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_throughput(bytes_per_sec: float) -> str:
    """Format throughput as human-readable rate."""
    if bytes_per_sec <= 0:
        return "0 B/s"
    return f"{format_bytes(bytes_per_sec)}/s"


@dataclass
class RsyncStats:
    files_transferred: int = 0
    bytes_transferred: int = 0
    bytes_per_sec: float = 0.0
    duration: float = 0.0

    @property
    def effective_bytes_per_sec(self) -> float:
        return self.bytes_per_sec


def parse_rsync_stats(output: str, *, wall_seconds: float) -> RsyncStats:
    """Parse rsync --stats output (stdout and stderr combined)."""
    stats = RsyncStats()
    transferred_size: int | None = None
    total_file_size: int | None = None

    for line in output.splitlines():
        m = _RE_RSYNC_FILES.search(line)
        if m:
            stats.files_transferred = int(m.group(1).replace(",", ""))
            continue

        m = _RE_RSYNC_TRANSFERRED.search(line)
        if m:
            transferred_size = parse_byte_size(m.group(1), m.group(2) or "B")
            continue

        m = _RE_RSYNC_TOTAL_FILE.search(line)
        if m:
            total_file_size = parse_byte_size(m.group(1), m.group(2) or "B")
            continue

        m = _RE_RSYNC_SPEED.search(line)
        if m:
            stats.bytes_per_sec = float(m.group(1).replace(",", ""))

    stats.bytes_transferred = transferred_size or total_file_size or 0

    if stats.bytes_per_sec <= 0 and wall_seconds > 0 and stats.bytes_transferred > 0:
        stats.bytes_per_sec = stats.bytes_transferred / wall_seconds

    return stats


@dataclass
class TransferTotals:
    """Accumulated transfer metrics across multiple operations."""

    files: int = 0
    bytes: int = 0

    def add(self, rsync: RsyncStats) -> None:
        self.files += rsync.files_transferred
        self.bytes += rsync.bytes_transferred

    def throughput(self, wall_seconds: float) -> float:
        if wall_seconds <= 0:
            return 0.0
        return self.bytes / wall_seconds
