"""Coordinate client rsync and server Borg via a staging lock on the NAS."""

from __future__ import annotations

import json
import logging
import os
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCK_FILENAME = ".harvestwind-client.lock"


def staging_lock_path(staging_dir: str | Path) -> Path:
    """Lock file lives beside the rsync tree (parent of ``backup_path``)."""
    return Path(staging_dir).resolve().parent / LOCK_FILENAME


def wait_for_staging_unlock(
    backup_path: str | Path,
    *,
    timeout: float = 10800,
    poll_interval: float = 30,
) -> tuple[bool, float]:
    """Wait until the client staging lock is absent. Returns (ok, seconds_waited)."""
    if timeout <= 0:
        return True, 0.0

    lock = staging_lock_path(backup_path)
    start = time.monotonic()
    while True:
        if not lock.is_file():
            waited = time.monotonic() - start
            if waited > 0:
                logger.info(
                    "Staging lock clear (%s) after %.1fs",
                    lock,
                    waited,
                )
            return True, waited

        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            logger.error(
                "Timed out after %.0fs waiting for staging lock: %s",
                timeout,
                lock,
            )
            return False, elapsed

        logger.info(
            "Waiting for client staging lock (%s, %.0fs / %.0fs)",
            lock,
            elapsed,
            timeout,
        )
        time.sleep(poll_interval)


def _ssh_base_cmd(dest: dict[str, Any]) -> list[str]:
    auth = dest["auth"]
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-i",
        auth["key_path"],
    ]
    port = dest.get("port", 22)
    if port != 22:
        cmd.extend(["-p", str(port)])
    cmd.append(f"{dest['user']}@{dest['host']}")
    return cmd


@dataclass
class RemoteStagingLock:
    """Create/remove a staging lock on the rsync destination host over SSH."""

    server_destination: dict[str, Any]
    hostname: str = ""

    def __post_init__(self) -> None:
        self.hostname = self.hostname or socket.gethostname()
        self.lock_path = staging_lock_path(self.server_destination["remote_path"])

    def acquire(self) -> None:
        payload = json.dumps(
            {
                "hostname": self.hostname,
                "pid": os.getpid(),
                "started": datetime.now(timezone.utc).isoformat(),
            }
        )
        remote_lock = str(self.lock_path)
        quoted_lock = shlex.quote(remote_lock)
        quoted_payload = shlex.quote(payload)
        shell = (
            f"test ! -e {quoted_lock} || exit 1; "
            f"printf '%s' {quoted_payload} > {quoted_lock}"
        )
        cmd = [*_ssh_base_cmd(self.server_destination), shell]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise StagingLockError(
                f"Could not acquire staging lock {remote_lock}: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        logger.info("Acquired staging lock on %s", remote_lock)

    def release(self) -> None:
        remote_lock = str(self.lock_path)
        cmd = [
            *_ssh_base_cmd(self.server_destination),
            f"rm -f {shlex.quote(remote_lock)}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.warning(
                "Failed to release staging lock %s: %s",
                remote_lock,
                (result.stderr or result.stdout).strip(),
            )
            return
        logger.info("Released staging lock on %s", remote_lock)

    def __enter__(self) -> RemoteStagingLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class StagingLockError(Exception):
    pass


def client_staging_lock(
    rsync_config: dict[str, Any],
    *,
    hostname: str | None = None,
) -> RemoteStagingLock | None:
    """Return a remote lock context manager when SSH staging lock is enabled."""
    lock_cfg = rsync_config.get("staging_lock") or {}
    if not lock_cfg.get("enabled", True):
        return None
    dest = rsync_config.get("server_destination") or {}
    if dest.get("type") != "ssh" or not dest.get("remote_path"):
        return None
    if not dest.get("auth", {}).get("key_path"):
        return None
    return RemoteStagingLock(dest, hostname=hostname or socket.gethostname())
