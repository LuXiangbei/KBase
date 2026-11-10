from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from ..cache import CACHE
from ..config import Settings
from ..deps import ConnDep, SettingsDep
from ..metrics import METRICS
from ..retrieval.index import get_index
from ..tracing import get as get_trace
from ..tracing import recent as recent_traces
from ..agent.tools import TOOL_SPECS

router = APIRouter(tags=["admin"])


@router.get("/metrics")
def metrics(conn: sqlite3.Connection = ConnDep, settings: Settings = SettingsDep) -> dict:
    snapshot = METRICS.snapshot()
    snapshot["index_chunks"] = len(get_index(settings.embed_dim))
    snapshot["documents"] = conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE deleted_at IS NULL"
    ).fetchone()["c"]
    snapshot["cache"] = CACHE.stats()
    return snapshot


@router.get("/cache")
def cache_stats() -> dict:
    return CACHE.stats()


@router.get("/traces")
def traces(limit: int = 20) -> dict:
    items = recent_traces(max(1, min(limit, 100)))
    return {"items": items, "total": len(items)}


@router.get("/traces/{trace_id}")
def trace_detail(trace_id: str) -> dict:
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="trace 不存在")
    return trace


@router.get("/tools")
def tools() -> dict:
    return {"items": TOOL_SPECS, "whitelist": sorted(TOOL_SPECS.keys())}


@router.post("/eval/run")
def run_eval(conn: sqlite3.Connection = ConnDep, settings: Settings = SettingsDep) -> dict:
    from ..eval.runner import run_evaluation

    report = run_evaluation(conn, settings)
    return report
