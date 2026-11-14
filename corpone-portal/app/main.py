import hashlib
import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.auth import purge_expired
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.gateway.inbound import gateway_app
from app.request_log import RequestLog
from app.routers import admin, auth, signups
from app.seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_PORT_PREFIX = re.compile(r"^/ports/\d+(?=/|$)")


class PortProxyPrefix:
    """Accept requests forwarded as /ports/<n>/... by the dev environment's proxy (with or without the prefix)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            m = _PORT_PREFIX.match(scope["path"])
            if m and not scope.get("root_path"):
                # ASGI: path keeps the full URL; root_path names the mount prefix Starlette strips for routing.
                scope = dict(scope, root_path=m.group(0))
        await self.app(scope, receive, send)
STATIC = Path(__file__).resolve().parent.parent / "static"
# Scripts and styles are linked as static/v/<content hash>/<name>, so every change gets a new URL and
# no browser or CDN (e.g. CloudFront in front of the dev proxy) can pair a new page with an old script.
VERSIONED = ("app.js", "admin.js", "styles.css")
_ASSET_LINK = re.compile(r'(["\'])static/(' + "|".join(map(re.escape, VERSIONED)) + r')\1')


def _fingerprint(name: str) -> str:
    return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]


def _page(name: str) -> HTMLResponse:
    """Serve an HTML page with fingerprinted asset links; the page itself is never cached."""
    html = (STATIC / name).read_text(encoding="utf-8")
    html = _ASSET_LINK.sub(lambda m: f"{m[1]}static/v/{_fingerprint(m[2])}/{m[2]}{m[1]}", html)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


class RevalidatedStaticFiles(StaticFiles):
    """Other static files: caches must check with the server (ETag) before reusing them."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app() -> FastAPI:
    if get_settings().fixed_otp_email:
        logging.getLogger(__name__).warning(
            "TEST MODE: %s always signs in with the fixed CORPONE_DEV_FIXED_OTP code. Never use this in production.",
            get_settings().fixed_otp_email)
    elif get_settings().dev_fixed_otp:
        logging.getLogger(__name__).warning(
            "CORPONE_DEV_FIXED_OTP is set but CORPONE_DEV_FIXED_OTP_EMAIL is not, so test mode is off.")
    app = FastAPI(title="CorpOne Corporate Bulk Line Portal", version="1.0.0")
    app.add_middleware(PortProxyPrefix)
    if get_settings().request_log:
        app.add_middleware(RequestLog)  # added last = outermost, so it times the whole request
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        purge_expired(db)
    app.include_router(auth.router)
    app.include_router(signups.router)
    app.include_router(admin.router)
    app.mount("/gateway", gateway_app)  # partner & AI gateway; docs at /gateway/docs

    @app.get("/static/v/{version}/{name}", include_in_schema=False)
    def versioned_asset(version: str, name: str):
        if name not in VERSIONED:
            raise HTTPException(404)
        # Any version serves the current file; the URL only exists to defeat stale caches.
        cache = "public, max-age=31536000, immutable" if version == _fingerprint(name) else "no-store"
        return FileResponse(STATIC / name, headers={"Cache-Control": cache})

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return _page("index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return _page("admin.html")

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"ok": True}

    return app


app = create_app()
