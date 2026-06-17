"""
KidGateService — domain logic tying UniFi, scheduler, persistence and notifications.

State model (two independent UniFi policies, per SPIKE-FINDINGS.md):
  • ad-hoc policy   — schedule ALWAYS; enabled => kids blocked right now ("Block now")
  • scheduled policy — UniFi-side schedule 23:30–06:00; enabled => bedtime block active

Effective:  blocked = adhoc_on OR (scheduled_on AND within_bedtime_window)
Precedence (§8): manual block > override > schedule.
  - "Block/Allow now" and temp-blocks act on the AD-HOC policy.
  - "Override bedtime" disables the SCHEDULED policy for a grace window, then re-enables it.

Persistence (§8): expiry actions are real APScheduler jobs in the SQLite jobstore, so
they survive a container restart; on boot we also run a reconcile pass.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from . import db
from .config import Settings
from .notify import Notifier
from .unifi import UnifiProvider, UnifiUnreachable

log = logging.getLogger("kidgate.service")


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; we always store UTC, so re-attach it."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

JOB_TEMP_BLOCK = "temp_block_expire"
JOB_OVERRIDE = "override_expire"

_SERVICE: "KidGateService | None" = None  # for APScheduler-serializable job targets


# Module-level job targets (importable by reference → picklable for the jobstore).
async def _run_temp_block_expire() -> None:
    if _SERVICE:
        await _SERVICE._on_temp_block_expire()


async def _run_override_expire() -> None:
    if _SERVICE:
        await _SERVICE._on_override_expire()


class KidGateService:
    def __init__(self, settings: Settings, unifi: UnifiProvider, notifier: Notifier) -> None:
        global _SERVICE
        self.s = settings
        self.unifi = unifi
        self.notifier = notifier
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(engine=db.get_engine())},
            timezone=str(settings.tz),
        )
        _SERVICE = self

    # ── lifecycle ──────────────────────────────────────────────────────
    async def start(self) -> None:
        self.scheduler.start()
        await self.reconcile()

    async def reconcile(self) -> None:
        """On boot, align DB timer records with reality; expired timers are dropped (§8)."""
        try:
            now = db.now_utc()
            for kind in ("temp_block", "override"):
                t = db.get_active_timer(kind)
                if t and _as_utc(t.expires_at) <= now:
                    log.info("Reconcile: %s timer already expired, restoring", kind)
                    if kind == "temp_block":
                        await self._on_temp_block_expire()
                    else:
                        await self._on_override_expire()
            log.info("Reconcile complete")
        except UnifiUnreachable as e:
            log.warning("Reconcile deferred — UniFi unreachable: %s", e)

    # ── time helpers ───────────────────────────────────────────────────
    def within_bedtime(self, when: datetime | None = None) -> bool:
        when = (when or db.now_utc()).astimezone(self.s.tz)
        start = time(self.s.bedtime_hour, self.s.bedtime_minute)
        end = time(6, 0)
        t = when.time()
        if start <= end:  # same-day window
            return start <= t < end
        return t >= start or t < end  # window crosses midnight (the normal case)

    def next_bedtime(self, when: datetime | None = None) -> datetime:
        when = (when or db.now_utc()).astimezone(self.s.tz)
        candidate = when.replace(hour=self.s.bedtime_hour, minute=self.s.bedtime_minute,
                                 second=0, microsecond=0)
        if candidate <= when:
            candidate += timedelta(days=1)
        return candidate

    # ── status (§5.4) ──────────────────────────────────────────────────
    async def status(self) -> dict:
        adhoc = await self.unifi.get_policy(self.s.adhoc_block_policy_id)
        sched = await self.unifi.get_policy(self.s.scheduled_block_policy_id)
        in_bed = self.within_bedtime()
        blocked = adhoc.enabled or (sched.enabled and in_bed)

        temp_t = db.get_active_timer("temp_block")
        over_t = db.get_active_timer("override")

        if adhoc.enabled:
            source = "temp_block" if temp_t else "manual"
            expires = temp_t.expires_at if temp_t else None
        elif sched.enabled and in_bed:
            source = "schedule"
            expires = None
        elif over_t:
            source = "override"
            expires = over_t.expires_at
        else:
            source = "allowed"
            expires = None

        if expires is not None:
            expires = _as_utc(expires)  # so the browser parses the UTC offset, not local

        return {
            "blocked": blocked,
            "source": source,
            "expires_at": expires.isoformat() if expires else None,
            "adhoc_enabled": adhoc.enabled,
            "scheduled_enabled": sched.enabled,
            "within_bedtime": in_bed,
            "bedtime": f"{self.s.bedtime_hour:02d}:{self.s.bedtime_minute:02d}",
        }

    # ── actions ────────────────────────────────────────────────────────
    async def block_now(self, actor: str) -> None:
        await self.unifi.set_enabled(self.s.adhoc_block_policy_id, True)
        self._cancel(JOB_TEMP_BLOCK)
        db.clear_active_timer("temp_block")
        db.record_audit(actor, "block_now", self.s.adhoc_block_policy_id, "blocked")
        await self.notifier.send("KidGate", f"Internet BLOCKED by {actor}", tags="no_entry")

    async def allow_now(self, actor: str) -> None:
        await self.unifi.set_enabled(self.s.adhoc_block_policy_id, False)
        self._cancel(JOB_TEMP_BLOCK)
        db.clear_active_timer("temp_block")
        db.record_audit(actor, "allow_now", self.s.adhoc_block_policy_id, "allowed")
        await self.notifier.send("KidGate", f"Internet ALLOWED by {actor}", tags="white_check_mark")

    async def temp_block(self, actor: str, minutes: int) -> datetime:
        await self.unifi.set_enabled(self.s.adhoc_block_policy_id, True)
        expires = db.now_utc() + timedelta(minutes=minutes)
        db.set_active_timer("temp_block", expires, actor)
        self._schedule(JOB_TEMP_BLOCK, _run_temp_block_expire, expires)
        db.record_audit(actor, "temp_block", self.s.adhoc_block_policy_id, "blocked",
                        f"{minutes} min, until {expires.isoformat()}")
        await self.notifier.send("KidGate", f"Internet blocked for {minutes} min by {actor}", tags="hourglass")
        return expires

    async def block_until_bedtime(self, actor: str) -> datetime:
        mins = max(1, int((self.next_bedtime() - db.now_utc()).total_seconds() // 60))
        return await self.temp_block(actor, mins)

    async def override_bedtime(self, actor: str, minutes: int) -> datetime:
        """Grant extra time by disabling the scheduled policy for a grace window (§5.3)."""
        await self.unifi.set_enabled(self.s.scheduled_block_policy_id, False)
        expires = db.now_utc() + timedelta(minutes=minutes)
        db.set_active_timer("override", expires, actor)
        self._schedule(JOB_OVERRIDE, _run_override_expire, expires)
        db.record_audit(actor, "override_bedtime", self.s.scheduled_block_policy_id, "allowed",
                        f"{minutes} min grace, until {expires.isoformat()}")
        await self.notifier.send("KidGate", f"Bedtime override: {minutes} more min by {actor}", tags="alarm_clock")
        return expires

    async def cancel_override(self, actor: str) -> None:
        await self._on_override_expire()
        db.record_audit(actor, "cancel_override", self.s.scheduled_block_policy_id, "")

    # ── expiry handlers ────────────────────────────────────────────────
    async def _on_temp_block_expire(self) -> None:
        await self.unifi.set_enabled(self.s.adhoc_block_policy_id, False)
        db.clear_active_timer("temp_block")
        db.record_audit("system", "temp_block_expired", self.s.adhoc_block_policy_id, "allowed")
        await self.notifier.send("KidGate", "Temporary block expired — internet restored", tags="white_check_mark")

    async def _on_override_expire(self) -> None:
        # Re-enable the schedule. Precedence: a manual block (adhoc) stays in force regardless.
        await self.unifi.set_enabled(self.s.scheduled_block_policy_id, True)
        db.clear_active_timer("override")
        db.record_audit("system", "override_expired", self.s.scheduled_block_policy_id, "")
        await self.notifier.send("KidGate", "Bedtime override ended — schedule resumed", tags="alarm_clock")

    # ── scheduler plumbing ─────────────────────────────────────────────
    def _schedule(self, job_id: str, func, run_date: datetime) -> None:
        self.scheduler.add_job(
            func, trigger=DateTrigger(run_date=run_date), id=job_id,
            replace_existing=True, misfire_grace_time=3600, coalesce=True,
        )

    def _cancel(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass
