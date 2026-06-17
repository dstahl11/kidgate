"""Lightweight auth (PRD §3): two named local users (admin + user), session cookie.
UniFi credentials never touch the browser — only these app accounts do.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from . import db

log = logging.getLogger("kidgate.auth")

# Stdlib PBKDF2-HMAC-SHA256 — no fragile third-party crypto deps. Format:
#   pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
_ITERATIONS = 240_000


def hash_password(p: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(p: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", p.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def seed_users(spec: str) -> None:
    """Seed users from APP_USERS = 'name:password:role,name2:password2:role'. Idempotent."""
    if not spec.strip():
        return
    with db.session() as s:
        for entry in spec.split(","):
            parts = entry.split(":")
            if len(parts) < 2:
                continue
            username, password = parts[0].strip(), parts[1]
            role = parts[2].strip() if len(parts) > 2 else "user"
            existing = s.scalar(select(db.User).where(db.User.username == username))
            if existing:
                existing.password_hash = hash_password(password)  # allow password rotation via env
                existing.role = role
            else:
                s.add(db.User(username=username, password_hash=hash_password(password), role=role))
        s.commit()


def authenticate(username: str, password: str) -> db.User | None:
    with db.session() as s:
        u = s.scalar(select(db.User).where(db.User.username == username))
        if u and verify_password(password, u.password_hash):
            return u
    return None


# ── FastAPI dependencies ────────────────────────────────────────────────
def current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user
