"""Discover Docker Compose apps and volumes for backup."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

LABEL_VOLUMES_EXCLUDE = "unifybackup.volumes.exclude"
LABEL_APP_EXCLUDE = "unifybackup.app.exclude"


@dataclass
class AppInfo:
    name: str
    path: Path
    compose_file: Path


class VolumeDiscovery:
    def get_volumes(self, compose_file: Path) -> list[str]:
        with compose_file.open() as fh:
            compose_data = yaml.safe_load(fh) or {}

        if "volumes" not in compose_data:
            return []

        if self._stack_excluded(compose_data):
            return []

        names: list[str] = []
        for volume_name, volume_config in compose_data["volumes"].items():
            if not self._is_named_volume(volume_name, volume_config):
                continue
            if self._volume_excluded(volume_config):
                continue
            if not self._volume_exists(volume_name):
                continue
            names.append(volume_name)
        return names

    def _stack_excluded(self, compose_data: dict) -> bool:
        for key in ("x-labels", "labels"):
            labels = compose_data.get(key) or {}
            if str(labels.get(LABEL_VOLUMES_EXCLUDE, "")).lower() == "true":
                return True
        return False

    def _volume_excluded(self, volume_config: dict | str) -> bool:
        if not isinstance(volume_config, dict):
            return False
        labels = volume_config.get("labels") or {}
        return str(labels.get(LABEL_VOLUMES_EXCLUDE, "")).lower() == "true"

    def _is_named_volume(self, name: str, config: dict | str) -> bool:
        if isinstance(config, dict) and config.get("external"):
            return True
        return not (isinstance(config, dict) and "driver" in config and name.startswith("./"))

    def _volume_exists(self, volume_name: str) -> bool:
        result = subprocess.run(
            ["docker", "volume", "inspect", volume_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0


def discover_apps(apps_root: Path) -> list[AppInfo]:
    apps: list[AppInfo] = []
    if not apps_root.is_dir():
        return apps

    for compose in sorted(apps_root.glob("*/docker-compose.yml")):
        app_dir = compose.parent
        name = app_dir.name
        try:
            with compose.open() as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            logger.warning("Skipping %s: %s", name, exc)
            continue

        labels = data.get("x-labels") or data.get("labels") or {}
        if str(labels.get(LABEL_APP_EXCLUDE, "")).lower() == "true":
            logger.info("Skipping app %s (excluded by label)", name)
            continue

        apps.append(AppInfo(name=name, path=app_dir, compose_file=compose))

    return apps
