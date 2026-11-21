"""Docker Compose argv used to stop and start a stack around volume backup."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ComposeAppConfig:
    """Per-app compose files and profiles. Defaults match a single compose file."""

    files: list[str] = field(default_factory=lambda: ["docker-compose.yml"])
    profiles: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> ComposeAppConfig:
        if not isinstance(data, dict):
            return cls()
        files = data.get("files") or ["docker-compose.yml"]
        profiles = data.get("profiles") or []
        return cls(
            files=[str(item) for item in files],
            profiles=[str(item) for item in profiles],
        )


def build_compose_command(
    app_path: Path, spec: ComposeAppConfig | None, *args: str
) -> list[str]:
    files = spec.files if spec is not None else ["docker-compose.yml"]
    profiles = spec.profiles if spec is not None else []
    cmd = ["docker", "compose"]
    for name in files:
        cmd.extend(["-f", str(app_path / name)])
    for profile in profiles:
        cmd.extend(["--profile", profile])
    cmd.extend(args)
    return cmd


def missing_compose_files(app_path: Path, spec: ComposeAppConfig | None) -> list[str]:
    files = spec.files if spec is not None else ["docker-compose.yml"]
    return [name for name in files if not (app_path / name).is_file()]
