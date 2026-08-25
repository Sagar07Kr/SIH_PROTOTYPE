"""FastAPI application.

Security posture (prototype, stated plainly in the README): AI calls happen
server-side only, secrets come from the environment, uploads are validated by
extension *and* magic bytes and capped by size, temporary files live under
`var/`, and every state change is written to an append-only audit log.
Authentication is an unimplemented interface, not a feature.
"""
from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.routes import router
from backend.config import settings
from backend.db import init_db
from backend.utils.errors import AppError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("layoutloom")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from backend.fonts.resolver import FontResolver
    missing = FontResolver().missing_scripts(["hi", "ar", "ja", "zh", "en"])
    if missing:
        log.warning("fonts missing for scripts %s -- run scripts/fetch_fonts.py; "
                    "those targets will render empty boxes", missing)
    log.info("provider=%s data_dir=%s", settings.ai_provider, settings.data_dir)
    yield


app = FastAPI(title="LayoutLoom", version="1.0.0",
              description="Layout-preserving PDF translation",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    log.info("app error %s: %s", exc.code, exc.message)
    return JSONResponse(status_code=exc.http_status, content=exc.as_dict())


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={
        "code": "INTERNAL", "message": "Something went wrong on the server.",
        "retryable": False})


app.include_router(router)


@app.get("/")
def root() -> dict:
    return {"name": "LayoutLoom", "docs": "/docs", "api": "/api/health"}
