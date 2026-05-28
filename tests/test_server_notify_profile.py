"""Server ntfy messages include profile / staging context."""

from unittest.mock import MagicMock, patch

from harvestwind_backup.config import (
    B2Config,
    BorgConfig,
    NtfyConfig,
    ServerConfig,
)
from harvestwind_backup.server.runner import ServerRunner


@patch("harvestwind_backup.server.runner.CloudSyncManager")
@patch("harvestwind_backup.server.runner.BorgManager")
def test_profile_label_from_config(_borg_cls: MagicMock, _cloud_cls: MagicMock) -> None:
    runner = ServerRunner(
        ServerConfig(
            profile="oxford-mini",
            borg=BorgConfig(
                repo_path="/srv/backups/oxford-mini/borg_repo",
                backup_path="/srv/backups/oxford-mini/rsync",
                compression="lz4",
            ),
            b2=B2Config(bucket="oxford-mini", path="backups"),
            ntfy=NtfyConfig(enabled=False),
        )
    )
    assert runner.profile_label == "oxford-mini"
    assert runner._notify_title("Server backup complete") == "Server backup complete (oxford-mini)"
    assert "**Profile:** `oxford-mini`" in runner._notify_body_prefix()
    assert "**Staging:** `/srv/backups/oxford-mini/rsync`" in runner._notify_body_prefix()


@patch("harvestwind_backup.server.runner.CloudSyncManager")
@patch("harvestwind_backup.server.runner.BorgManager")
def test_profile_label_derived_from_backup_path(
    _borg_cls: MagicMock, _cloud_cls: MagicMock
) -> None:
    runner = ServerRunner(
        ServerConfig(
            borg=BorgConfig(
                repo_path="/srv/backups/nixihost/borg_repo",
                backup_path="/srv/backups/nixihost/rsync",
                compression="lz4",
            ),
            b2=B2Config(bucket="nixihost", path="backups"),
            ntfy=NtfyConfig(enabled=False),
        )
    )
    assert runner.profile_label == "nixihost"


@patch("harvestwind_backup.server.runner.wait_for_staging_unlock", return_value=(True, 0.0))
@patch("harvestwind_backup.server.runner.NtfyNotifier")
@patch("harvestwind_backup.server.runner.BorgManager")
@patch("harvestwind_backup.server.runner.CloudSyncManager")
def test_final_notify_includes_profile_in_title(
    _cloud_cls: MagicMock,
    borg_cls: MagicMock,
    ntfy_cls: MagicMock,
    _wait_lock: MagicMock,
) -> None:
    borg = borg_cls.return_value
    archive = MagicMock(
        name="2026-05-25_05-00-00",
        size_orig=1,
        size_deduplicated=1,
        num_files=1,
    )
    borg.create_backup.return_value = (True, archive)
    borg.prune_repository.return_value = (True, None)
    borg.verify_repository.return_value = (True, 0.1)
    borg.repo_info.return_value = {"total_archives": 1, "total_size": 100}

    _cloud_cls.return_value.sync.return_value = (True, MagicMock(bytes_transferred=0))
    _cloud_cls.return_value.verify.return_value = (True, MagicMock(duration=0.1))

    notifier = ntfy_cls.return_value
    runner = ServerRunner(
        ServerConfig(
            profile="nixihost",
            borg=BorgConfig(
                repo_path="/srv/backups/nixihost/borg_repo",
                backup_path="/srv/backups/nixihost/rsync",
                compression="lz4",
                retention=None,
            ),
            b2=B2Config(bucket="nixihost", path="backups"),
            ntfy=NtfyConfig(enabled=True),
        )
    )
    runner.run()

    final_call = notifier.notify_if.call_args_list[-1]
    assert final_call[0][1] == "Server backup complete (nixihost)"
    assert "**Profile:** `nixihost`" in final_call[0][2]
    assert "nixihost" in final_call[1]["tags"]
