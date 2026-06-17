# Deploying KidGate to your Docker server

KidGate ships as source + a Dockerfile. The server builds the image locally; the
only secret-bearing file (`.env`) is created **on the server** and never committed
or baked into the image.

## Where credentials live (read this first)

`.env` sits next to `docker-compose.yml` on the server. It is:
- **gitignored** — never in version control
- **never in the image** — the Dockerfile copies only `app/` + `requirements.txt`
- **mounted read-only at runtime** — `./.env:/app/.env:ro`

That mount (instead of Compose's `env_file:`) is deliberate: it passes values
through raw, so secrets containing a literal `$` (like the UniFi password) are
not corrupted by Compose variable interpolation.

## Steps

1. **Copy the bundle to the server** (this folder, minus `.env`/`.venv`/`data`/`.git`).
   e.g. unpack `kidgate-deploy.tar.gz` into `/opt/kidgate` (or your stacks dir).

2. **Create `.env` on the server** in that same directory. Start from the template:
   ```bash
   cp .env.example .env
   ```
   Fill in your own values:
   ```
   UNIFI_HOST=192.168.1.1
   UNIFI_USERNAME=<your UniFi local-admin username>
   UNIFI_PASSWORD=<your UniFi local-admin password>
   UNIFI_SITE=default

   ADHOC_BLOCK_POLICY_ID=<ad-hoc block policy id>
   SCHEDULED_BLOCK_POLICY_ID=<scheduled bedtime policy id>
   KIDS_GROUP_ID=<kids client-group id>

   TIMEZONE=America/New_York
   SECRET_KEY=<run: openssl rand -base64 48>
   BEDTIME_HOUR=23
   BEDTIME_MINUTE=30
   APP_USERS=parent1:<password>:admin,parent2:<password>:admin

   NTFY_SERVER=https://ntfy.sh
   NTFY_TOPIC=
   NTFY_TOKEN=
   ```
   - `APP_USERS` format: `name:password:role` per user, **comma** between users.
     `role` is `admin` or `user`. No `:` or `,` inside a password.
   - Generate a unique `SECRET_KEY` per host (don't reuse the dev one).

3. **Lock down the file** so only the owner can read the secrets:
   ```bash
   chmod 600 .env
   ```

4. **Bring it up:**
   ```bash
   docker compose up -d --build
   ```

5. **Verify:**
   ```bash
   docker compose logs --tail 20 | grep "UniFi login"   # expect: UniFi login OK
   curl -s http://localhost:8099/healthz                 # expect: {"ok":true}
   ```
   Then open `http://<server-LAN-IP>:8099` on a phone and Add to Home Screen.

## Notes
- **LAN-only.** Do not port-forward 8099 to the internet. For remote access use
  Tailscale/VPN. To pin the bind to one interface, set the compose port to
  `"192.168.1.50:8099:8099"`.
- **State** (audit log, timers, user accounts) lives in the `kidgate-data` named
  volume and survives rebuilds. To reset users, recreate that volume.
- **Updating:** copy new source over, `docker compose up -d --build`. `.env` and
  the data volume are untouched.
- A harmless `variable is not set` warning from Compose (if a secret contains a
  literal `$`) is just it scanning `.env` for `${...}`; nothing flows through that
  path because the app reads `/app/.env` itself. Ignore it.
