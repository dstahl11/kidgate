"""KidGate FastAPI app (PRD §6). LAN-only; all UniFi access server-side."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, db
from .config import get_settings
from .notify import build_notifier
from .service import KidGateService
from .unifi import UnifiError, UnifiProvider, UnifiUnreachable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kidgate")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    db.init_db(s.db_path)
    auth.seed_users(s.app_users)
    unifi = UnifiProvider(s.base_url, s.unifi_username, s.unifi_password, s.unifi_site, s.unifi_verify_tls)
    notifier = build_notifier(s.ntfy_server, s.ntfy_topic, s.ntfy_token)
    service = KidGateService(s, unifi, notifier)
    try:
        await unifi.login()
    except UnifiError as e:
        log.warning("Initial UniFi login failed (will retry on demand): %s", e)
    await service.start()
    app.state.service = service
    app.state.unifi = unifi
    log.info("KidGate started")
    try:
        yield
    finally:
        service.scheduler.shutdown(wait=False)
        await unifi.aclose()


app = FastAPI(title="KidGate", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=get_settings().secret_key, same_site="strict", https_only=False)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def svc(request: Request) -> KidGateService:
    return request.app.state.service


# ── auth routes ──────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = auth.authenticate(username, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid login"}, status_code=401)
    request.session["user"] = {"name": user.username, "role": user.role}
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── UI ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "user": request.session["user"]})


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, user: dict = Depends(auth.require_admin)):
    return templates.TemplateResponse("audit.html", {"request": request, "user": user, "entries": db.recent_audit(200)})


# ── JSON API ─────────────────────────────────────────────────────────────
@app.get("/api/status")
async def api_status(request: Request, user: dict = Depends(auth.current_user)):
    try:
        return JSONResponse(await svc(request).status())
    except UnifiUnreachable as e:
        return JSONResponse({"error": "unreachable", "detail": str(e)}, status_code=502)
    except UnifiError as e:
        return JSONResponse({"error": "unifi", "detail": str(e)}, status_code=502)


def _actor(user: dict) -> str:
    return user.get("name", "unknown")


async def _do(request, user, coro):
    try:
        await coro
        return JSONResponse(await svc(request).status())
    except UnifiUnreachable as e:
        return JSONResponse({"error": "unreachable", "detail": str(e)}, status_code=502)
    except UnifiError as e:
        return JSONResponse({"error": "unifi", "detail": str(e)}, status_code=502)


@app.post("/api/block")
async def api_block(request: Request, user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).block_now(_actor(user)))


@app.post("/api/allow")
async def api_allow(request: Request, user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).allow_now(_actor(user)))


@app.post("/api/temp-block")
async def api_temp_block(request: Request, minutes: int = Form(...), user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).temp_block(_actor(user), minutes))


@app.post("/api/until-bedtime")
async def api_until_bedtime(request: Request, user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).block_until_bedtime(_actor(user)))


@app.post("/api/override")
async def api_override(request: Request, minutes: int = Form(...), user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).override_bedtime(_actor(user), minutes))


@app.post("/api/cancel-override")
async def api_cancel_override(request: Request, user: dict = Depends(auth.current_user)):
    return await _do(request, user, svc(request).cancel_override(_actor(user)))


@app.get("/healthz")
async def healthz():
    return {"ok": True}
