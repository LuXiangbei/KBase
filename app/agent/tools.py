from __future__ import annotations

import sqlite3

from ..db import dumps, new_id, now_iso
from ..security import User

CATEGORIES = {"hr", "legal", "it", "other"}
URGENCIES = {"low", "normal", "high"}


class ToolError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def audit(conn: sqlite3.Connection, user: User | None, action: str, detail: dict | str) -> None:
    conn.execute(
        "INSERT INTO audit (ts, user_id, action, detail) VALUES (?,?,?,?)",
        (now_iso(), user.id if user else None, action, dumps(detail) if isinstance(detail, dict) else str(detail)),
    )


def create_ticket(
    conn: sqlite3.Connection,
    user: User,
    request_id: str,
    category: str,
    summary: str,
    detail: str = "",
    urgency: str = "normal",
) -> dict:
    if not request_id:
        raise ToolError("MISSING_REQUEST_ID", "create_ticket 需要 request_id（幂等键）")
    if category not in CATEGORIES:
        raise ToolError("BAD_CATEGORY", f"category 必须是 {sorted(CATEGORIES)} 之一")
    if urgency not in URGENCIES:
        raise ToolError("BAD_URGENCY", f"urgency 必须是 {sorted(URGENCIES)} 之一")
    summary = (summary or "").strip()
    if not summary:
        raise ToolError("MISSING_SUMMARY", "summary 不能为空")

    existing = conn.execute(
        "SELECT * FROM tickets WHERE user_id=? AND request_id=?", (user.id, request_id)
    ).fetchone()
    if existing:
        return {**dict(existing), "deduped": True}

    ticket_id = new_id("tkt")
    try:
        conn.execute(
            "INSERT INTO tickets (id,user_id,request_id,category,summary,detail,urgency,status,created_at) "
            "VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (ticket_id, user.id, request_id, category, summary[:200], detail[:2000], urgency, now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT * FROM tickets WHERE user_id=? AND request_id=?", (user.id, request_id)
        ).fetchone()
        if row:
            return {**dict(row), "deduped": True}
        raise

    audit(conn, user, "ticket.create", {"ticket_id": ticket_id, "category": category})
    conn.commit()
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    return {**dict(row), "deduped": False}


def get_ticket_status(conn: sqlite3.Connection, user: User, ticket_id: str) -> dict:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise ToolError("NOT_FOUND", "工单不存在", status=404)
    if row["user_id"] != user.id and not user.has_role("hr"):
        audit(conn, user, "ticket.forbidden", {"ticket_id": ticket_id})
        conn.commit()
        raise ToolError("FORBIDDEN", "无权查看他人工单", status=403)
    return dict(row)


def handoff(conn: sqlite3.Connection, user: User, reason: str, summary: str = "") -> dict:
    audit(conn, user, "agent.handoff", {"reason": reason, "summary": summary[:300]})
    conn.commit()
    return {"status": "handoff", "reason": reason}


TOOL_WHITELIST = {
    "search_policy",
    "get_ticket_status",
    "create_ticket",
    "handoff",
}

# 暴露给前端/调用方的工具契约；也是"工具白名单"的可读版本
TOOL_SPECS: dict[str, dict] = {
    "search_policy": {
        "description": "在内网制度库里检索条款，返回带出处的片段。只读。",
        "parameters": {"query": "string", "top_k": "integer(默认 6)"},
        "idempotent": True,
        "side_effect": False,
    },
    "get_ticket_status": {
        "description": "按工单号查询进度；只能查自己或 HR 权限内的工单。",
        "parameters": {"ticket_id": "string(tkt_xxx)"},
        "idempotent": True,
        "side_effect": False,
    },
    "create_ticket": {
        "description": "创建人工工单。必须携带 request_id 做幂等；信息不全时不得调用。",
        "parameters": {
            "category": "enum(hr|legal|it|other)",
            "summary": "string(≤200)",
            "detail": "string",
            "urgency": "enum(low|normal|high)",
            "request_id": "string(幂等键)",
        },
        "idempotent": True,
        "side_effect": True,
    },
    "handoff": {
        "description": "转人工并记录原因。",
        "parameters": {"reason": "string", "summary": "string"},
        "idempotent": True,
        "side_effect": True,
    },
}


def ensure_tool_allowed(name: str) -> None:
    if name not in TOOL_WHITELIST:
        raise ToolError("TOOL_NOT_ALLOWED", f"工具 {name} 不在白名单内", status=403)
