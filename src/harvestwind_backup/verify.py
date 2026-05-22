"""Backup integrity verification."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TarVerifyResult:
    tar_valid: bool = False
    checksum_valid: bool = False
    checksum_found: bool = False
    errors: list[str] = field(default_factory=list)


def verify_volume_tar(tar_path: Path) -> TarVerifyResult:
    """Verify a volume backup tar and optional sidecar .sha256 (legacy parity)."""
    result = TarVerifyResult()
    if not tar_path.is_file():
        result.errors.append(f"Missing tar: {tar_path}")
        return result

    try:
        proc = subprocess.run(
            ["tar", "-tf", str(tar_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        result.tar_valid = True
        if not proc.stdout.strip():
            result.errors.append("tar archive is empty")
            result.tar_valid = False
    except subprocess.CalledProcessError as exc:
        result.errors.append(f"tar integrity check failed: {exc.stderr or exc}")
        return result

    checksum_file = Path(f"{tar_path}.sha256")
    if not checksum_file.is_file():
        result.errors.append(f"Missing checksum file: {checksum_file}")
        return result

    result.checksum_found = True
    try:
        stored = checksum_file.read_text().split()[0]
        digest = hashlib.sha256()
        with tar_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        result.checksum_valid = stored == digest.hexdigest()
        if not result.checksum_valid:
            result.errors.append("Checksum mismatch")
    except OSError as exc:
        result.errors.append(f"Checksum verification failed: {exc}")

    return result
