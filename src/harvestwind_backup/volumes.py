"""Docker named volume backups."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from .verify import verify_volume_tar

logger = logging.getLogger(__name__)


class VolumeManager:
    def __init__(self, max_backups: int = 5, uid: int = 1000, gid: int = 1000):
        self.max_backups = max_backups
        self.uid = uid
        self.gid = gid

    def backup_volume(self, volume_name: str, backup_path: Path) -> dict:
        result: dict = {
            "success": False,
            "volume": volume_name,
            "tar_path": None,
            "errors": [],
        }
        try:
            backup_path.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            tar_name = f"{date_str}_{volume_name}.tar"
            tar_path = backup_path / tar_name
            result["tar_path"] = str(tar_path)

            subprocess.run(
                ["chown", f"{self.uid}:{self.gid}", str(backup_path)],
                check=True,
            )
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{volume_name}:/volume_data:ro",
                    "-v",
                    f"{backup_path}:/backup",
                    "alpine",
                    "sh",
                    "-c",
                    (
                        f"cd /volume_data && tar --numeric-owner -cf /backup/{tar_name} . "
                        f"&& chown {self.uid}:{self.gid} /backup/{tar_name}"
                    ),
                ],
                check=True,
            )
            checksum = self._sha256(tar_path)
            (backup_path / f"{tar_name}.sha256").write_text(f"{checksum}  {tar_name}\n")
            verify = verify_volume_tar(tar_path)
            result["verified"] = verify.tar_valid and verify.checksum_valid
            if not result["verified"]:
                result["errors"].extend(verify.errors)
                logger.error(
                    "Volume backup verification failed for %s: %s",
                    volume_name,
                    "; ".join(verify.errors),
                )
                return result
            self._prune_old(volume_name, backup_path)
            result["success"] = True
        except (subprocess.CalledProcessError, OSError) as exc:
            msg = f"Volume backup failed for {volume_name}: {exc}"
            logger.error(msg)
            result["errors"].append(msg)
        return result

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _prune_old(self, volume_name: str, backup_path: Path) -> None:
        archives = sorted(
            backup_path.glob(f"*_{volume_name}.tar"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in archives[self.max_backups :]:
            old.unlink(missing_ok=True)
            old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)
