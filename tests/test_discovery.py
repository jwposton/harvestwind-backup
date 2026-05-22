"""App and volume discovery."""

from pathlib import Path
from unittest.mock import patch

import yaml

from harvestwind_backup.discovery import VolumeDiscovery, app_backup_excluded, discover_apps


def test_app_exclude_on_service_skips_stack(tmp_path: Path) -> None:
    app = tmp_path / "mealie"
    app.mkdir()
    compose = {
        "services": {
            "mealie": {"image": "mealie"},
            "postgres": {
                "image": "postgres",
                "labels": {"unifybackup.app.exclude": "true"},
            },
        },
        "volumes": {"mealie-data": None},
    }
    (app / "docker-compose.yml").write_text(yaml.dump(compose))
    assert discover_apps(tmp_path) == []


def test_volume_tmpfs_and_bind_excluded(tmp_path: Path) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        yaml.dump(
            {
                "volumes": {
                    "./cache": None,
                    "ram": {"driver": "tmpfs"},
                    "bound": {"type": "bind"},
                    "data": None,
                }
            }
        )
    )
    discovery = VolumeDiscovery()
    with patch.object(discovery, "_volume_exists", return_value=True):
        assert discovery.get_volumes(compose_file) == ["data"]


def test_app_backup_excluded_stack_x_labels() -> None:
    data = {"x-labels": {"unifybackup.app.exclude": "true"}, "services": {}}
    assert app_backup_excluded(data) is True
