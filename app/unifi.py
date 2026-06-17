"""
UnifiProvider — the SINGLE adapter for all UniFi controller access (PRD §4.1).

If Ubiquiti changes endpoints in a firmware update, this is the one file to fix.
Validated against: UDM Pro SE, UniFi OS 5.1.15, Network 10.4.57 (see SPIKE-FINDINGS.md).

Control primitive: toggle an object-oriented-network-config policy's `enabled` flag.
  - List:   GET  /proxy/network/v2/api/site/{site}/object-oriented-network-configs   (plural)
  - Update: PUT  /proxy/network/v2/api/site/{site}/object-oriented-network-config/{id} (singular)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("kidgate.unifi")


class UnifiError(Exception):
    """Base for all UniFi adapter errors."""


class UnifiUnreachable(UnifiError):
    """Controller could not be reached (network/TLS). Never silently treated as success (§8)."""


class UnifiAuthError(UnifiError):
    """Login failed or session could not be re-established."""


@dataclass
class Policy:
    id: str
    name: str
    enabled: bool
    raw: dict  # full object — needed to PUT updates back faithfully


class UnifiProvider:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        site: str = "default",
        verify_tls: bool = False,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url
        self._username = username
        self._password = password
        self._site = site
        self._csrf: str | None = None
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    @property
    def _list_path(self) -> str:
        return f"/proxy/network/v2/api/site/{self._site}/object-oriented-network-configs"

    def _item_path(self, policy_id: str) -> str:
        return f"/proxy/network/v2/api/site/{self._site}/object-oriented-network-config/{policy_id}"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── auth ──────────────────────────────────────────────────────────
    async def login(self) -> None:
        try:
            r = await self._client.post(
                "/api/auth/login",
                json={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise UnifiUnreachable(f"Cannot reach UniFi controller: {e}") from e
        if r.status_code in (401, 403):
            raise UnifiAuthError("UniFi login rejected — check UNIFI_USERNAME / UNIFI_PASSWORD.")
        r.raise_for_status()
        self._csrf = r.headers.get("x-csrf-token") or r.headers.get("x-updated-csrf-token")
        if self._csrf:
            self._client.headers["X-CSRF-Token"] = self._csrf
        log.info("UniFi login OK (csrf=%s)", "present" if self._csrf else "missing")

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Issue a request, logging in first if needed and retrying once on 401 (§8)."""
        async with self._lock:
            if self._csrf is None:
                await self.login()
            try:
                r = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as e:
                raise UnifiUnreachable(f"UniFi request failed: {e}") from e
            if r.status_code == 401:
                log.info("UniFi 401 — re-authenticating and retrying once")
                self._csrf = None
                await self.login()
                try:
                    r = await self._client.request(method, path, **kwargs)
                except httpx.HTTPError as e:
                    raise UnifiUnreachable(f"UniFi request failed after re-login: {e}") from e
            return r

    # ── policy operations ─────────────────────────────────────────────
    @staticmethod
    def _parse(obj: dict) -> Policy:
        pid = obj.get("_id") or obj.get("id") or ""
        return Policy(id=pid, name=obj.get("name", "(unnamed)"), enabled=bool(obj.get("enabled")), raw=obj)

    async def list_policies(self) -> list[Policy]:
        r = await self._request("GET", self._list_path)
        r.raise_for_status()
        body = r.json()
        data = body.get("data", body) if isinstance(body, dict) else body
        return [self._parse(o) for o in data]

    async def get_policy(self, policy_id: str) -> Policy:
        for p in await self.list_policies():
            if p.id == policy_id:
                return p
        raise UnifiError(f"Policy {policy_id} not found on controller.")

    async def set_enabled(self, policy_id: str, enabled: bool) -> Policy:
        """Toggle a policy's enabled flag. Sends the full object back (required by the API)."""
        current = await self.get_policy(policy_id)
        if current.enabled == enabled:
            return current  # already in desired state — idempotent
        body = dict(current.raw)
        body["enabled"] = enabled
        r = await self._request("PUT", self._item_path(policy_id), json=body)
        if r.status_code >= 400:
            raise UnifiError(f"Failed to set policy {policy_id} enabled={enabled}: HTTP {r.status_code} {r.text[:200]}")
        log.info("Policy %s set enabled=%s", policy_id, enabled)
        updated = r.json()
        data = updated.get("data", updated) if isinstance(updated, dict) else updated
        if isinstance(data, list) and data:
            data = data[0]
        return self._parse(data) if isinstance(data, dict) else await self.get_policy(policy_id)
