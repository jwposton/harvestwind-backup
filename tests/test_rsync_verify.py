"""Rsync verify mode and skip behavior."""

from unittest.mock import MagicMock, patch

from harvestwind_backup.rsync import RsyncManager


def _manager(**verify) -> RsyncManager:
    return RsyncManager(
        {
            "server_destination": {
                "type": "ssh",
                "host": "backup.example.com",
                "user": "backup",
                "remote_path": "/srv/backups/rsync",
                "auth": {"key_path": "/tmp/key"},
                "options": {"checksum": False},
            },
            "verify": verify,
        }
    )


@patch("harvestwind_backup.rsync.subprocess.run")
def test_verify_uses_mtime_size_by_default(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    manager = _manager()
    ok, _ = manager.verify_app("immich", "/apps/immich")
    assert ok is True
    cmd = mock_run.call_args[0][0]
    assert "--dry-run" in cmd
    assert "-i" in cmd
    assert "--checksum" not in cmd


@patch("harvestwind_backup.rsync.subprocess.run")
def test_verify_checksum_when_configured(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    manager = _manager(checksum=True)
    manager.verify_app("immich", "/apps/immich")
    cmd = mock_run.call_args[0][0]
    assert "--checksum" in cmd


@patch("harvestwind_backup.rsync.subprocess.run")
def test_skip_verify_when_unchanged(mock_run: MagicMock) -> None:
    manager = _manager(skip_if_unchanged=True)
    with patch.object(manager, "verify_app") as verify:
        with patch.object(
            manager,
            "sync",
            return_value=(True, MagicMock(files_transferred=0, duration=1.0)),
        ):
            ok, _, backup_secs, verify_secs = manager.backup_app(
                "immich", "/apps/immich"
            )
    assert ok is True
    assert verify_secs == 0.0
    verify.assert_not_called()


@patch("harvestwind_backup.rsync.subprocess.run")
def test_verify_runs_when_files_transferred(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    manager = _manager(skip_if_unchanged=True)
    with patch.object(
        manager,
        "sync",
        return_value=(True, MagicMock(files_transferred=3, duration=2.0)),
    ):
        manager.backup_app("immich", "/apps/immich")
    assert mock_run.called


def test_verify_config_merge() -> None:
    manager = RsyncManager(
        {
            "verify": {"checksum": False, "skip_if_unchanged": True},
            "server_destination": {
                "path": "/dest",
                "verify": {"skip_if_unchanged": False},
            },
        }
    )
    cfg = manager._verify_config_for(manager.server_destination)
    assert cfg.skip_if_unchanged is False
    assert cfg.checksum is False
