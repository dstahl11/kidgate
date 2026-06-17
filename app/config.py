"""Configuration loaded from environment / .env (PRD §7)."""
from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── UniFi controller ──
    unifi_host: str = "192.168.1.1"
    unifi_username: str
    unifi_password: str
    unifi_site: str = "default"
    unifi_verify_tls: bool = False

    # ── Policy mapping (verified via spike) ──
    kids_group_id: str = ""
    adhoc_block_policy_id: str
    scheduled_block_policy_id: str

    # ── App ──
    timezone: str = "America/New_York"
    secret_key: str = "change-me"
    bedtime_hour: int = 23
    bedtime_minute: int = 30
    db_path: str = "data/kidgate.db"
    # Local app accounts: "name:password:role,name2:password2:role" (role: admin|user)
    app_users: str = ""

    # ── Notifications (ntfy) ──
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def base_url(self) -> str:
        host = self.unifi_host.strip().rstrip("/")
        return f"https://{host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
