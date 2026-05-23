"""Server runner behavior."""

from unittest.mock import MagicMock, patch

from harvestwind_backup.config import (
    B2Config,
    BorgConfig,
    BorgRetention,
    NtfyConfig,
    ServerConfig,
)
from harvestwind_backup.server.runner import ServerRunner


def _server_config() -> ServerConfig:
    return ServerConfig(
        lock_timeout=300,
        borg=BorgConfig(
            repo_path="/srv/backups/nixihost/borg_repo",
            backup_path="/srv/backups/nixihost/rsync",
            compression="lz4",
            retention=BorgRetention(),
        ),
        b2=B2Config(bucket="nixihost", path="backups"),
        ntfy=NtfyConfig(enabled=False),
    )


@patch("harvestwind_backup.server.runner.NtfyNotifier")
@patch("harvestwind_backup.server.runner.BorgManager")
@patch("harvestwind_backup.server.runner.CloudSyncManager")
def test_cloud_sync_uses_borg_repo_and_verifies(
    cloud_cls: MagicMock,
    borg_cls: MagicMock,
    _ntfy_cls: MagicMock,
) -> None:
    borg = borg_cls.return_value
    borg.create_backup.return_value = (True, None)
    borg.prune_repository.return_value = (True, None)
    borg.verify_repository.return_value = (True, 1.0)
    borg.repo_info.return_value = {"total_archives": 3, "total_size": 1000}

    cloud = cloud_cls.return_value
    cloud.sync.return_value = (True, MagicMock(bytes_transferred=0))
    cloud.verify.return_value = (True, MagicMock(bytes_transferred=0))

    runner = ServerRunner(_server_config())
    runner.run()

    cloud.sync.assert_called_once_with("/srv/backups/nixihost/borg_repo")
    cloud.verify.assert_called_once_with("/srv/backups/nixihost/borg_repo")
    borg.verify_repository.assert_called_once()


@patch("harvestwind_backup.server.runner.NtfyNotifier")
@patch("harvestwind_backup.server.runner.BorgManager")
@patch("harvestwind_backup.server.runner.CloudSyncManager")
def test_skips_b2_when_borg_create_fails(
    cloud_cls: MagicMock,
    borg_cls: MagicMock,
    _ntfy_cls: MagicMock,
) -> None:
    borg = borg_cls.return_value
    borg.create_backup.return_value = (False, None)

    runner = ServerRunner(_server_config())
    runner.run()

    cloud_cls.return_value.sync.assert_not_called()
