"""Staging lock coordination between client rsync and server Borg."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from harvestwind_backup.staging_lock import (
    RemoteStagingLock,
    client_staging_lock,
    staging_lock_path,
    wait_for_staging_unlock,
)


def test_staging_lock_path_beside_rsync_tree() -> None:
    assert staging_lock_path("/srv/backups/nixihost/rsync") == Path(
        "/srv/backups/nixihost/.harvestwind-client.lock"
    )


def test_wait_for_staging_unlock_immediate() -> None:
    lock = staging_lock_path("/tmp/example/rsync")
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        lock.unlink()
    ok, waited = wait_for_staging_unlock("/tmp/example/rsync", timeout=5, poll_interval=0.1)
    assert ok is True
    assert waited < 0.1


def test_wait_for_staging_unlock_times_out(tmp_path: Path) -> None:
    staging = tmp_path / "rsync"
    staging.mkdir()
    lock = staging_lock_path(staging)
    lock.write_text("busy")
    ok, waited = wait_for_staging_unlock(staging, timeout=0.2, poll_interval=0.05)
    assert ok is False
    assert waited >= 0.2


@patch("harvestwind_backup.staging_lock.subprocess.run")
def test_remote_staging_lock_acquire_and_release(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    dest = {
        "type": "ssh",
        "host": "backup.example.com",
        "user": "backup",
        "port": 22,
        "remote_path": "/srv/backups/nixihost/rsync",
        "auth": {"key_path": "/tmp/key"},
    }
    with RemoteStagingLock(dest, hostname="client-a") as lock:
        assert lock.lock_path.name == ".harvestwind-client.lock"
    assert mock_run.call_count == 2


def test_client_staging_lock_disabled() -> None:
    assert (
        client_staging_lock(
            {
                "server_destination": {"type": "ssh", "remote_path": "/x"},
                "staging_lock": {"enabled": False},
            }
        )
        is None
    )
