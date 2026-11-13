import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import purge_expired
from app.db import Base, SessionLocal, engine
from app.gateway.inbound import gateway_app
from app.routers import admin, auth, signups
from app.seed import seed

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="CorpOne Corporate Bulk Line Portal", version="1.0.0")
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
