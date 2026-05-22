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
def test_cloud_sync_uses_borg_repo_not_rsync_staging(
    cloud_cls: MagicMock,
    borg_cls: MagicMock,
    _ntfy_cls: MagicMock,
) -> None:
    borg = borg_cls.return_value
    borg.create_backup.return_value = (True, None)
    borg.prune_repository.return_value = (True, None)

    cloud = cloud_cls.return_value
    cloud.sync.return_value = (True, MagicMock(bytes_transferred=0))
    cloud.verify.return_value = (True, MagicMock(bytes_transferred=0))

    runner = ServerRunner(_server_config())
    runner.run()

    cloud.sync.assert_called_once_with("/srv/backups/nixihost/borg_repo")
    cloud.verify.assert_called_once_with("/srv/backups/nixihost/borg_repo")
