# KidGate — UniFi Spike Findings

**Date:** 2026-06-17
**Method:** Read-only inspection of the live UDM via SSH (root) + controller MongoDB (`ace` db, port 27117). No changes made.

## Validated environment (PRD §8 — record the firmware we built against)

| Component | Version |
|-----------|---------|
| Model | UniFi Dream Machine SE (UDM Pro SE) |
| UniFi OS firmware | 5.1.15 |
| UniFi Network application | 10.4.57.0-g19f85adec |
| Host | UDM LAN IP (e.g. `192.168.1.1`) |
| Internal site key | `default` |

## CORRECTION to first pass

A first scan of the **legacy** collections (`firewall_policy`, `usergroup`, `traffic_rule`, `firewallrule`) found them empty and I wrongly concluded nothing was set up. In fact this controller uses **Network 10's new Policy Engine** ("object-oriented network config"), which stores everything in different collections. The foundation IS set up.

## Confirmed control targets (this is what the app toggles)

### Kids client group
- Collection: `network_members_groups`
- `_id`: **`<KIDS_GROUP_ID>`**, name **"Kids"**, `type: 1`
- N MAC members (the kids' devices — laptops, tablets, etc.; several may be randomized-MAC entries to verify).

### Scheduled bedtime "No Internet" policy  ← the existing block (PRD §5.3)
- Collection: `object_oriented_network_config`
- `_id`: **`<SCHEDULED_BLOCK_POLICY_ID>`**, name **"Kids"**, `enabled: true`
- `targets: ["<KIDS_GROUP_ID>"]` (`target_type: 4` = client group)
- `secure.internet_mode: 3` (= **No Internet**)
- `secure.schedule_from_internet: { mode: "EVERY_DAY", time_range_start: "23:30", time_range_end: "06:00" }`

### Ad-hoc "Block now" policy  ← DOES NOT EXIST YET
Needed for one-tap block/allow regardless of time. Plan: a second `object_oriented_network_config` object — same Kids target, `secure.internet_mode: 3`, **schedule mode ALWAYS**, `enabled: false` by default. "Block now" = set `enabled: true`; "Allow now" = `enabled: false`.

## Data model → control primitives

| App action | Mechanism |
|-----------|-----------|
| Block now | set ad-hoc policy `enabled: true` |
| Allow now | set ad-hoc policy `enabled: false` |
| Override bedtime (grace) | set scheduled policy `enabled: false`, app re-enables after grace window |
| Read live status | GET both policies' `enabled` + scheduled window math (TZ America/New_York) |
| Precedence (§8) | manual block > override > schedule |

## REST contract — PROVEN against live API (control path, per §4.1) ✅

Auth: `POST https://<UNIFI_HOST>/api/auth/login` with `{username, password}` → sets session cookie + returns `X-CSRF-Token` header. A dedicated local admin (role: Network = Site Admin, local-access-only, no MFA) works.

| Operation | Method | Path | Notes |
|-----------|--------|------|-------|
| List policies | `GET` | `/proxy/network/v2/api/site/default/object-oriented-network-configs` (**plural**) | returns array of objects |
| Update policy | `PUT` | `/proxy/network/v2/api/site/default/object-oriented-network-config/{id}` (**singular**) | send full object with `enabled` flipped; returns 200 |

- Send the **full object** back on PUT (round-trip with `dict(target)` + flipped `enabled` returned HTTP 200). Plural path + `/{id}` 404s — item ops use the singular base.
- CSRF token must be echoed as `X-CSRF-Token` on the PUT.
- Verified 2026-06-17: enable→disable round-trip on Ad-Hoc Block returned 200/200 and restored cleanly. Read confirms Nightime untouched.

## Status: UniFi integration FULLY DE-RISKED ✅
All `.env` IDs populated and verified. Ready to build the real `UnifiProvider` + app against this proven contract.
