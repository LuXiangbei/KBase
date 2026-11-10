from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.tools import CATEGORIES, ToolError, create_ticket
from ..deps import ConnDep, UserDep
from ..security import User

router = APIRouter(tags=["tickets"])


class TicketCreate(BaseModel):
    category: str = Field(default="other")
    summary: str = Field(min_length=1, max_length=200)
    detail: str = ""
    urgency: str = "normal"
    request_id: str = Field(min_length=4, max_length=80)


@router.post("/tickets")
def create(payload: TicketCreate, conn: sqlite3.Connection = ConnDep, user: User = UserDep) -> dict:
    if payload.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category 必须是 {sorted(CATEGORIES)} 之一")
    try:
        return create_ticket(
            conn,
            user,
            request_id=payload.request_id,
            category=payload.category,
            summary=payload.summary,
            detail=payload.detail,
            urgency=payload.urgency,
        )
    except ToolError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/tickets")
def list_mine(conn: sqlite3.Connection = ConnDep, user: User = UserDep) -> dict:
    rows = conn.execute(
        "SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user.id,)
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


@router.get("/tickets/{ticket_id}")
def get_one(ticket_id: str, conn: sqlite3.Connection = ConnDep, user: User = UserDep) -> dict:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="工单不存在")
    if row["user_id"] != user.id and "hr" not in user.roles:
        raise HTTPException(status_code=403, detail="无权查看他人工单")
    return dict(row)
