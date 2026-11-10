from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import admin, chat, docs, health, tickets
from .config import Settings, get_settings
from .db import init_db
from .retrieval.index import get_index

WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        init_db(settings.db_path)
        get_index(settings.embed_dim).reload(settings.db_path)
        yield

    app = FastAPI(
        title="KBase · 企业制度知识库 Agentic RAG",
        description="检索增强 + 工具调用 + 引用可追溯，离线可运行。",
        version=settings.version,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (health.router, docs.router, chat.router, tickets.router, admin.router):
        app.include_router(router, prefix="/api")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # noqa: ANN001
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
