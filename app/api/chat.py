from __future__ import annotations

import json
import sqlite3
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent import orchestrator
from ..agent.tools import audit
from ..config import Settings
from ..db import db_session
from ..deps import ConnDep, SettingsDep, UserDep
from ..metrics import METRICS
from ..security import User

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    request_id: str | None = None


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
def chat(
    payload: ChatRequest,
    conn: sqlite3.Connection = ConnDep,
    settings: Settings = SettingsDep,
    user: User = UserDep,
) -> dict:
    turn = orchestrator.build_turn(
        conn, user, payload.message, session_id=payload.session_id, request_id=payload.request_id, settings=settings
    )
    text = "".join(turn.stream())
    result = orchestrator.finalize_turn(conn, turn, user, payload.message, settings)
    result["text"] = text
    result["insufficient"] = turn.insufficient
    METRICS.record_turn(
        result["latency_ms"].get("total_ms", 0.0),
        turn.usage.get("in", 0),
        turn.usage.get("out", 0),
        cost=result.get("cost_cny", 0.0),
    )
    return result


async def _stream_events(turn, user: User, settings: Settings, request: Request) -> AsyncIterator[str]:
    started = time.perf_counter()
    first_token: float | None = None
    last_ping = started
    yield sse(
        "meta",
        {
            "session_id": turn.session_id,
            "message_id": "",
            "intent": turn.intent,
            "citations": turn.citations,
            "insufficient": turn.insufficient,
        },
    )
    if turn.state in {"NEED_SLOT", "HANDOFF"}:
        yield sse("agent", {"state": turn.state, "slots": turn.slots, "question": turn.text})

    try:
        for piece in turn.stream():
            if await request.is_disconnected():
                with db_session(settings.db_path) as conn:
                    audit(conn, user, "chat.disconnect", {"intent": turn.intent, "chars": len(turn.text)})
                    conn.commit()
                return
            if first_token is None:
                first_token = time.perf_counter()
            yield sse("delta", {"text": piece})
            now = time.perf_counter()
            if now - last_ping > 15:
                last_ping = now
                yield ": ping\n\n"
    except Exception as exc:  # noqa: BLE001
        METRICS.record_error()
        with db_session(settings.db_path) as conn:
            audit(conn, user, "chat.error", {"error": str(exc)[:300]})
            conn.commit()
        yield sse("error", {"code": "GENERATION_FAILED", "message": str(exc)[:300]})
        return

    with db_session(settings.db_path) as conn:
        done = orchestrator.finalize_turn(conn, turn, user, "", settings)
    total_ms = round((time.perf_counter() - started) * 1000, 2)
    done["latency_ms"]["total_ms"] = total_ms
    done["latency_ms"]["ttft_ms"] = round((first_token - started) * 1000, 2) if first_token else None
    METRICS.record_turn(
        total_ms,
        turn.usage.get("in", 0),
        turn.usage.get("out", 0),
        cost=done.get("cost_cny", 0.0),
        ttft_ms=done["latency_ms"]["ttft_ms"],
    )
    yield sse("done", done)


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    conn: sqlite3.Connection = ConnDep,
    settings: Settings = SettingsDep,
    user: User = UserDep,
) -> StreamingResponse:
    turn = orchestrator.build_turn(
        conn, user, payload.message, session_id=payload.session_id, request_id=payload.request_id, settings=settings
    )
    return StreamingResponse(
        _stream_events(turn, user, settings, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
