"""Borg verify and list."""

from unittest.mock import MagicMock, patch

from harvestwind_backup.borg import BorgManager


@patch("harvestwind_backup.borg.subprocess.run")
def test_verify_latest_archive(mock_run: MagicMock, tmp_path) -> None:
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="borg 1.2\n", stderr=""),  # version
        MagicMock(returncode=0, stdout="2026-01-01_00-00-00\n", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    manager = BorgManager(
        tmp_path / "repo",
        tmp_path / "data",
        "lz4",
    )
    ok, duration = manager.verify_repository(archive_name="2026-01-01_00-00-00")
    assert ok is True
    assert duration >= 0
    check_cmd = mock_run.call_args_list[-1][0][0]
    assert check_cmd[:2] == ["borg", "check"]
    assert "::2026-01-01_00-00-00" in check_cmd[-1]
