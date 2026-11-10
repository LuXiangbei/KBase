from __future__ import annotations

import sqlite3
from datetime import date

from ..security import User, can_read_document


def allowed_documents(conn: sqlite3.Connection, user: User, today: date | None = None) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM documents WHERE deleted_at IS NULL AND status = 'ready'"
    ).fetchall()
    return [row for row in rows if can_read_document(row, user, today)]


def allowed_chunk_ids(conn: sqlite3.Connection, user: User, today: date | None = None) -> set[str]:
    """ACL 下推到候选集：越权文档的 chunk 根本不会进入检索。"""
    docs = allowed_documents(conn, user, today)
    if not docs:
        return set()
    doc_ids = [row["id"] for row in docs]
    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"SELECT id FROM chunks WHERE doc_id IN ({placeholders})", doc_ids
    ).fetchall()
    return {row["id"] for row in rows}
