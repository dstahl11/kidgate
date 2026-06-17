"""Notifier seam (PRD §5.6). v1 ships an ntfy implementation; the interface keeps
notifications swappable. A no-op is used when no topic is configured.
"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger("kidgate.notify")


class Notifier(Protocol):
    async def send(self, title: str, message: str, tags: str = "") -> None: ...


class NullNotifier:
    async def send(self, title: str, message: str, tags: str = "") -> None:
        return None


class NtfyNotifier:
    def __init__(self, server: str, topic: str, token: str = "") -> None:
        self._url = f"{server.rstrip('/')}/{topic}"
        self._token = token

    async def send(self, title: str, message: str, tags: str = "") -> None:
        headers = {"Title": title}
        if tags:
            headers["Tags"] = tags
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                await c.post(self._url, content=message.encode("utf-8"), headers=headers)
        except httpx.HTTPError as e:  # notifications must never break the control flow
            log.warning("ntfy send failed: %s", e)


def build_notifier(server: str, topic: str, token: str = "") -> Notifier:
    return NtfyNotifier(server, topic, token) if topic else NullNotifier()
