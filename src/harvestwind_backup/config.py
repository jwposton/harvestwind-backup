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
class BorgRetention:
    """GFS-style keep rules passed to ``borg prune`` (0 = omit that tier)."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 6
    yearly: int = 0

    @classmethod
    def from_dict(cls, data: Any) -> "BorgRetention":
        if not isinstance(data, dict):
            return cls()
        return cls(
            daily=int(data.get("daily", 7)),
            weekly=int(data.get("weekly", 4)),
            monthly=int(data.get("monthly", 6)),
            yearly=int(data.get("yearly", 0)),
        )


@dataclass
class BorgConfig:
    repo_path: str
    backup_path: str
    compression: str
    full_check: bool = False
    cache_dir: str | None = None
    prune: bool = True
    retention: BorgRetention | None = field(default_factory=BorgRetention)


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
        prune = bool(borg.get("prune", True))
        retention_raw = borg.get("retention")
        if retention_raw is False or not prune:
            retention = None
        elif retention_raw is None:
            retention = BorgRetention()
        else:
            retention = BorgRetention.from_dict(retention_raw)
        return cls(
            borg=BorgConfig(
                repo_path=borg["repo_path"],
                backup_path=borg["backup_path"],
                compression=borg.get("compression", "lz4"),
                full_check=bool(borg.get("full_check", False)),
                cache_dir=borg.get("cache_dir"),
                prune=prune,
                retention=retention,
            ),
            b2=B2Config(**backup["b2"]),
            ntfy=NtfyConfig.from_dict(backup.get("ntfy")),
            lock_timeout=int(server.get("lock_timeout", 300)),
        )
