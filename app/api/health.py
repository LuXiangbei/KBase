from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..config import Settings
from ..deps import ConnDep, SettingsDep
from ..providers import describe_providers

router = APIRouter(tags=["health"])


@router.get("/health")
def health(conn: sqlite3.Connection = ConnDep, settings: Settings = SettingsDep) -> dict:
    chunks = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
    docs = conn.execute("SELECT COUNT(*) AS c FROM documents WHERE deleted_at IS NULL").fetchone()["c"]
    return {
        "status": "ok",
        "version": settings.version,
        "db": "ok",
        "documents": docs,
        "index_chunks": chunks,
        "providers": describe_providers(settings),
        "port": settings.port,
    }
