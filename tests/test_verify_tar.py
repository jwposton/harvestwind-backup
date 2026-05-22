"""Volume tar verification."""

import hashlib
import subprocess

from harvestwind_backup.verify import verify_volume_tar


def test_verify_volume_tar_ok(tmp_path) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("ok")
    tar = tmp_path / "20260101_mealie-data.tar"
    subprocess.run(
        ["tar", "-cf", str(tar), "-C", str(tmp_path), "payload.txt"],
        check=True,
    )
    digest = hashlib.sha256(tar.read_bytes()).hexdigest()
    (tmp_path / f"{tar.name}.sha256").write_text(f"{digest}  {tar.name}\n")

    result = verify_volume_tar(tar)
    assert result.tar_valid
    assert result.checksum_valid
