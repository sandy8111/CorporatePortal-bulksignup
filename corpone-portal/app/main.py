import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
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
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(STATIC / "admin.html")

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"ok": True}

    return app


app = create_app()
