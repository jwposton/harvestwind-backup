"""Cloud sync rclone command."""

from unittest.mock import MagicMock, patch

from harvestwind_backup.cloud import CloudSyncManager, _RCLONE_SYNC_FLAGS


def _success(**kwargs) -> MagicMock:
    return MagicMock(returncode=0, stdout="", stderr="", **kwargs)


@patch("harvestwind_backup.cloud.subprocess.run")
@patch("harvestwind_backup.cloud.Path.exists", return_value=True)
def test_sync_uses_legacy_rclone_flags(_exists: MagicMock, mock_run: MagicMock) -> None:
    mock_run.return_value = _success()
    manager = CloudSyncManager("nixihost", "backups", max_retries=0)

    manager.sync("/srv/backups/nixihost/borg_repo")

    cmd = mock_run.call_args[0][0]
    assert cmd[0:4] == [
        "rclone",
        "sync",
        "/srv/backups/nixihost/borg_repo",
        "b2:nixihost/backups",
    ]
    for flag in _RCLONE_SYNC_FLAGS:
        assert flag in cmd
    assert "--delete-after" in cmd
    assert "--bwlimit" in cmd


@patch("harvestwind_backup.cloud.subprocess.run")
@patch("harvestwind_backup.cloud.Path.exists", return_value=True)
def test_verify_runs_check_one_way(_exists: MagicMock, mock_run: MagicMock) -> None:
    mock_run.return_value = _success()
    manager = CloudSyncManager("nixihost", "backups", max_retries=0)

    ok, _ = manager.verify("/srv/backups/nixihost/borg_repo")

    assert ok is True
    cmd = mock_run.call_args[0][0]
    assert cmd[:5] == [
        "rclone",
        "check",
        "/srv/backups/nixihost/borg_repo",
        "b2:nixihost/backups",
        "--one-way",
    ]


@patch("harvestwind_backup.cloud.time.sleep")
@patch("harvestwind_backup.cloud.subprocess.run")
@patch("harvestwind_backup.cloud.Path.exists", return_value=True)
def test_retries_on_failure(
    _exists: MagicMock, mock_run: MagicMock, _sleep: MagicMock
) -> None:
    mock_run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="temporary"),
        _success(),
    ]
    manager = CloudSyncManager("nixihost", "backups", max_retries=2)

    ok, _ = manager.sync("/srv/backups/nixihost/borg_repo")

    assert ok is True
    assert mock_run.call_count == 2
    _sleep.assert_called_once_with(1)


@patch("harvestwind_backup.cloud.time.sleep")
@patch("harvestwind_backup.cloud.subprocess.run")
@patch("harvestwind_backup.cloud.Path.exists", return_value=True)
def test_sync_fails_after_retries_exhausted(
    _exists: MagicMock, mock_run: MagicMock, _sleep: MagicMock
) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="down")
    manager = CloudSyncManager("nixihost", "backups", max_retries=2)

    ok, _ = manager.sync("/srv/backups/nixihost/borg_repo")

    assert ok is False
    assert mock_run.call_count == 3
    assert _sleep.call_count == 2
