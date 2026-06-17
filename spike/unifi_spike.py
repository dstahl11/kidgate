#!/usr/bin/env python3
"""
KidGate UniFi spike — de-risks the §4 integration BEFORE we build the app.

Goal: prove we can (1) log into the UDM, (2) read firewall policies and their
`enabled` flag, and optionally (3) flip one policy's `enabled` flag and flip it
back. This validates the exact endpoints/shapes for the live firmware so the
real UnifiProvider is a known quantity, not a guess.

Read-only by default. Pass --toggle <policy_id> to do a live enable→disable
round-trip on ONE policy (it restores the original state).

Usage:
    ../.venv/bin/python unifi_spike.py            # login + list policies
    ../.venv/bin/python unifi_spike.py --toggle <POLICY_ID>
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

# ── Minimal .env loader (no external dep) ─────────────────────────────
def load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        sys.exit(f"ERROR: {env_path} not found. Copy .env.example to .env and fill it in.")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


load_env()

HOST = os.environ["UNIFI_HOST"].strip().rstrip("/")
USER = os.environ["UNIFI_USERNAME"]
PASS = os.environ["UNIFI_PASSWORD"]
SITE = os.environ.get("UNIFI_SITE", "default")
VERIFY = os.environ.get("UNIFI_VERIFY_TLS", "false").lower() in ("1", "true", "yes")
BASE = f"https://{HOST}"


def log(msg: str) -> None:
    print(msg, flush=True)


def login(client: httpx.Client) -> str:
    """POST creds to UniFi OS, return the CSRF token. Cookie is stored on client."""
    log(f"→ Logging in to {BASE}/api/auth/login as {USER!r} (verify_tls={VERIFY})")
    r = client.post("/api/auth/login", json={"username": USER, "password": PASS})
    if r.status_code == 401:
        sys.exit("✗ Login failed (401). Check UNIFI_USERNAME / UNIFI_PASSWORD.")
    r.raise_for_status()
    # CSRF token comes back in a response header on UniFi OS; cookie is set automatically.
    csrf = r.headers.get("x-csrf-token") or r.headers.get("x-updated-csrf-token") or ""
    cookies = ", ".join(client.cookies.jar._cookies.keys()) if hasattr(client.cookies.jar, "_cookies") else ""
    log(f"✓ Login OK. CSRF token: {'present' if csrf else 'NOT FOUND in headers'}  Cookie domains: {cookies}")
    return csrf


# Candidate endpoints — this controller (Network 10.4) uses the Policy Engine's
# object-oriented-network-config objects. We try that first, then fall back to
# legacy paths in case of firmware variation. Spike confirmed via mongo that the
# Kids policies live in `object_oriented_network_config`.
POLICY_ENDPOINTS = [
    ("v2 object-oriented-network-configs", "GET", f"/proxy/network/v2/api/site/{SITE}/object-oriented-network-configs"),
    ("v2 object-oriented-network-config",  "GET", f"/proxy/network/v2/api/site/{SITE}/object-oriented-network-config"),
    ("v2 firewall-policies",               "GET", f"/proxy/network/v2/api/site/{SITE}/firewall-policies"),
    ("v1 firewallrule",                    "GET", f"/proxy/network/api/s/{SITE}/rest/firewallrule"),
]


def find_policies(client: httpx.Client) -> tuple[str, list[dict]]:
    """Try each candidate endpoint; return (endpoint_used, policies) for the first that works."""
    for label, _method, path in POLICY_ENDPOINTS:
        try:
            r = client.get(path)
        except httpx.HTTPError as e:
            log(f"  · {label}: request error {e!r}")
            continue
        if r.status_code != 200:
            log(f"  · {label}: HTTP {r.status_code}")
            continue
        try:
            body = r.json()
        except Exception:
            log(f"  · {label}: 200 but non-JSON body")
            continue
        # v1 wraps in {"data": [...]}, v2 returns a bare list.
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, list):
            log(f"✓ {label}: {len(data)} item(s) at {path}")
            return path, data
        log(f"  · {label}: 200 but unexpected shape: {type(data)}")
    sys.exit("✗ Could not find a working firewall-policy endpoint. Capture this output for diagnosis.")


def summarize(policies: list[dict]) -> None:
    log("\n── Firewall policies ─────────────────────────────────────────────")
    for p in policies:
        pid = p.get("_id") or p.get("id") or "?"
        name = p.get("name") or p.get("description") or "(unnamed)"
        enabled = p.get("enabled")
        action = p.get("action") or p.get("ruleset") or ""
        log(f"  [{pid}]  enabled={enabled!s:<5}  action={action:<8}  {name}")
    log("──────────────────────────────────────────────────────────────────")
    log("Identify the AD-HOC block and SCHEDULED block policies above, then")
    log("put their IDs into .env (ADHOC_BLOCK_POLICY_ID / SCHEDULED_BLOCK_POLICY_ID).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--toggle", metavar="POLICY_ID", help="Live enable→disable round-trip on one policy")
    args = ap.parse_args()

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    with httpx.Client(base_url=BASE, verify=VERIFY, timeout=15.0, headers=headers, follow_redirects=True) as client:
        csrf = login(client)
        if csrf:
            client.headers["X-CSRF-Token"] = csrf

        endpoint, policies = find_policies(client)
        summarize(policies)

        if not args.toggle:
            return

        target = next((p for p in policies if (p.get("_id") or p.get("id")) == args.toggle), None)
        if not target:
            sys.exit(f"✗ Policy {args.toggle} not found in {endpoint}.")
        original = bool(target.get("enabled"))
        log(f"\n→ Round-trip test on [{args.toggle}] (currently enabled={original})")

        # Per-item ops use the SINGULAR base (the UI's `a`), NOT the plural list path.
        update_base = f"/proxy/network/v2/api/site/{SITE}/object-oriented-network-config"

        def set_enabled(state: bool) -> None:
            body = dict(target)
            body["enabled"] = state
            r = client.put(f"{update_base}/{args.toggle}", json=body)
            if r.status_code >= 400:
                log(f"  ✗ HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            log(f"  ✓ set enabled={state} (HTTP {r.status_code})")

        set_enabled(not original)
        time.sleep(1.5)
        set_enabled(original)
        log("✓ Round-trip complete; policy restored to original state.")


if __name__ == "__main__":
    main()
