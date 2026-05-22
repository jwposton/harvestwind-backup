"""Load client and server YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .notify.ntfy import NtfyConfig


@dataclass
class VolumeConfig:
    max_backups: int = 5
    backup_dir: str = "vol_bkup"
    uid: int = 1000
    gid: int = 1000


@dataclass
class ClientConfig:
    apps_root: str
    volumes: VolumeConfig
    rsync: dict[str, Any]
    ntfy: NtfyConfig

    @classmethod
    def from_yaml(cls, path: Path) -> "ClientConfig":
        with path.open() as fh:
            data = yaml.safe_load(fh)
        backup = data["backup"]
        return cls(
            apps_root=backup["apps_root"],
            volumes=VolumeConfig(**backup.get("volumes", {})),
            rsync=backup.get("rsync", {}),
            ntfy=NtfyConfig.from_dict(backup.get("ntfy")),
        )


@dataclass
class BorgConfig:
    repo_path: str
    backup_path: str
    compression: str
    full_check: bool = False
    cache_dir: str | None = None


@dataclass
class B2Config:
    bucket: str
    path: str


@dataclass
class ServerConfig:
    borg: BorgConfig
    b2: B2Config
    ntfy: NtfyConfig
    lock_timeout: int = 300

    @classmethod
    def from_yaml(cls, path: Path) -> "ServerConfig":
        with path.open() as fh:
            data = yaml.safe_load(fh)
        backup = data["backup"]
        server = backup.get("server", {})
        borg = backup["borg"]
        return cls(
            borg=BorgConfig(
                repo_path=borg["repo_path"],
                backup_path=borg["backup_path"],
                compression=borg.get("compression", "lz4"),
                full_check=bool(borg.get("full_check", False)),
                cache_dir=borg.get("cache_dir"),
            ),
            b2=B2Config(**backup["b2"]),
            ntfy=NtfyConfig.from_dict(backup.get("ntfy")),
            lock_timeout=int(server.get("lock_timeout", 300)),
        )
