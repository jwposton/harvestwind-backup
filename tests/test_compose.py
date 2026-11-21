"""Compose command construction and client config parsing."""

from pathlib import Path

import yaml

from harvestwind_backup.compose import ComposeAppConfig, build_compose_command
from harvestwind_backup.config import ClientConfig


def test_build_compose_command_default() -> None:
    cmd = build_compose_command(Path("/apps/mealie"), None, "up", "-d")
    assert cmd == [
        "docker",
        "compose",
        "-f",
        "/apps/mealie/docker-compose.yml",
        "up",
        "-d",
    ]


def test_build_compose_command_files_and_profiles() -> None:
    spec = ComposeAppConfig(
        files=["docker-compose.yml", "docker-compose.override.yml"],
        profiles=["discuss"],
    )
    cmd = build_compose_command(Path("/apps/clutch-alerts"), spec, "down")
    assert cmd == [
        "docker",
        "compose",
        "-f",
        "/apps/clutch-alerts/docker-compose.yml",
        "-f",
        "/apps/clutch-alerts/docker-compose.override.yml",
        "--profile",
        "discuss",
        "down",
    ]


def test_client_config_compose_section(tmp_path: Path) -> None:
    path = tmp_path / "client.yml"
    path.write_text(
        yaml.dump(
            {
                "backup": {
                    "apps_root": "/home/jwp/docker-services",
                    "compose": {
                        "clutch-alerts": {
                            "files": [
                                "docker-compose.yml",
                                "docker-compose.override.yml",
                            ],
                            "profiles": ["discuss"],
                        }
                    },
                }
            }
        )
    )
    cfg = ClientConfig.from_yaml(path)
    spec = cfg.compose["clutch-alerts"]
    assert spec.files == ["docker-compose.yml", "docker-compose.override.yml"]
    assert spec.profiles == ["discuss"]


def test_client_config_without_compose_section(tmp_path: Path) -> None:
    path = tmp_path / "client.yml"
    path.write_text(
        yaml.dump({"backup": {"apps_root": "/home/jwp/docker-services"}})
    )
    cfg = ClientConfig.from_yaml(path)
    assert cfg.compose == {}
