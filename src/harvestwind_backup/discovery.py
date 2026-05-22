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

_BIND_MOUNT_NAME_PREFIXES = (".", "/", "$", "~")


@dataclass
class AppInfo:
    name: str
    path: Path
    compose_file: Path


def _label_is_true(labels: object, key: str) -> bool:
    if not labels:
        return False
    if isinstance(labels, dict):
        return str(labels.get(key, "")).lower() == "true"
    if isinstance(labels, list):
        return any(str(item) == f"{key}=true" for item in labels)
    return False


def app_backup_excluded(compose_data: dict) -> bool:
    """Skip entire stack if app.exclude is set on stack labels or any service."""
    for key in ("x-labels", "labels"):
        if _label_is_true(compose_data.get(key), LABEL_APP_EXCLUDE):
            logger.info("Stack excluded by %s on compose root", key)
            return True

    for service_name, service in (compose_data.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        if _label_is_true(service.get("labels"), LABEL_APP_EXCLUDE):
            logger.info(
                "Stack excluded: service %s has %s",
                service_name,
                LABEL_APP_EXCLUDE,
            )
            return True
    return False


class VolumeDiscovery:
    def get_volumes(self, compose_file: Path) -> list[str]:
        with compose_file.open() as fh:
            compose_data = yaml.safe_load(fh) or {}

        if "volumes" not in compose_data:
            return []

        if self._stack_volumes_excluded(compose_data):
            return []

        names: list[str] = []
        for volume_name, volume_config in compose_data["volumes"].items():
            if not self._is_named_volume(volume_name, volume_config):
                logger.debug("Skipping %s: not a named Docker volume", volume_name)
                continue
            if self._volume_excluded(volume_config):
                continue
            if not self._volume_exists(volume_name):
                continue
            names.append(volume_name)
        return names

    def _stack_volumes_excluded(self, compose_data: dict) -> bool:
        for key in ("x-labels", "labels"):
            if _label_is_true(compose_data.get(key), LABEL_VOLUMES_EXCLUDE):
                return True
        return False

    def _volume_excluded(self, volume_config: dict | str) -> bool:
        if not isinstance(volume_config, dict):
            return False
        labels = volume_config.get("labels") or {}
        return _label_is_true(labels, LABEL_VOLUMES_EXCLUDE)

    def _is_named_volume(self, name: str, config: dict | str | None) -> bool:
        if name.startswith(_BIND_MOUNT_NAME_PREFIXES):
            return False
        if config is None:
            return True
        if not isinstance(config, dict):
            return True
        if config.get("driver") == "tmpfs":
            return False
        if config.get("type") == "bind":
            return False
        return True

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

        if app_backup_excluded(data):
            logger.info("Skipping app %s (excluded by label)", name)
            continue

        apps.append(AppInfo(name=name, path=app_dir, compose_file=compose))

    return apps
