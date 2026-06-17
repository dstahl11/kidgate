"""SQLite persistence (PRD §5.5, §6): users, audit log, and active-timer state.

APScheduler owns the actual expiry *jobs* in its own table (see scheduler.py);
this module persists the human-facing state we reconcile and display, plus the
audit trail and local user accounts.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # "admin" | "user"


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64), default="")
    result_state: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class Timer(Base):
    """A single active temp-block or bedtime-override, for display + restart reconciliation.

    kind: "temp_block" (ad-hoc policy ON, auto-restore at expiry)
          "override"   (scheduled policy OFF for a grace window, auto-restore at expiry)
    """
    __tablename__ = "timers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


_engine = None


def init_db(db_path: str):
    global _engine
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("DB not initialized — call init_db() first.")
    return _engine


def session() -> Session:
    return Session(get_engine())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── audit ──────────────────────────────────────────────────────────────
def record_audit(actor: str, action: str, target: str = "", result_state: str = "", detail: str = "") -> None:
    with session() as s:
        s.add(AuditEntry(ts=now_utc(), actor=actor, action=action, target=target,
                         result_state=result_state, detail=detail))
        s.commit()


def recent_audit(limit: int = 100) -> list[AuditEntry]:
    with session() as s:
        return list(s.scalars(select(AuditEntry).order_by(AuditEntry.ts.desc()).limit(limit)))


# ── timers ─────────────────────────────────────────────────────────────
def set_active_timer(kind: str, expires_at: datetime, created_by: str) -> None:
    """Replace any active timer of this kind with a new one."""
    with session() as s:
        for t in s.scalars(select(Timer).where(Timer.kind == kind, Timer.active.is_(True))):
            t.active = False
        s.add(Timer(kind=kind, expires_at=expires_at, created_by=created_by,
                    created_at=now_utc(), active=True))
        s.commit()


def clear_active_timer(kind: str) -> None:
    with session() as s:
        for t in s.scalars(select(Timer).where(Timer.kind == kind, Timer.active.is_(True))):
            t.active = False
        s.commit()


def get_active_timer(kind: str) -> Timer | None:
    with session() as s:
        return s.scalars(
            select(Timer).where(Timer.kind == kind, Timer.active.is_(True)).order_by(Timer.id.desc())
        ).first()
