"""Push backup notifications to ntfy."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "min": "min",
    "low": "low",
    "default": "default",
    "high": "high",
    "urgent": "urgent",
    "success": "low",
    "warning": "default",
    "failure": "high",
}


@dataclass
class NtfyConfig:
    enabled: bool = False
    url: str = ""
    topic: str = "backups"
    token: str = ""
    notify_on: dict[str, bool] = field(
        default_factory=lambda: {"success": True, "failure": True, "warning": True}
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NtfyConfig":
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            url=str(data.get("url", "")).rstrip("/"),
            topic=str(data.get("topic", "backups")),
            token=str(data.get("token") or os.environ.get("NTFY_TOKEN", "")),
            notify_on=dict(
                data.get(
                    "notify_on",
                    {"success": True, "failure": True, "warning": True},
                )
            ),
        )

    def should_notify(self, kind: str) -> bool:
        if not self.enabled or not self.url or not self.topic:
            return False
        return self.notify_on.get(kind, True)


class NtfyNotifier:
    def __init__(self, config: NtfyConfig, *, hostname: str | None = None):
        self.config = config
        self.hostname = hostname or "backup"

    def send(
        self,
        title: str,
        body: str,
        *,
        priority: str = "default",
        tags: list[str] | None = None,
    ) -> bool:
        if not body.strip():
            return False
        if not self.config.enabled:
            return False

        headers = {
            "Title": f"{self.hostname}: {title}"[:250],
            "Priority": PRIORITY_MAP.get(priority, priority),
            "Content-Type": "text/markdown; charset=utf-8",
        }
        if tags:
            headers["Tags"] = ",".join(tags)[:200]
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        url = f"{self.config.url}/{self.config.topic}"
        try:
            response = requests.post(url, data=body, headers=headers, timeout=30)
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error("ntfy notification failed: %s", exc)
            return False

    def notify_if(self, kind: str, title: str, body: str, **kwargs: Any) -> bool:
        if not self.config.should_notify(kind):
            return False
        return self.send(title, body, priority=kind, **kwargs)
