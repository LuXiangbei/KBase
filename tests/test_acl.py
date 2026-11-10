from __future__ import annotations

import json

from app.retrieval.filters import allowed_chunk_ids, allowed_documents
from app.security import User, can_read_document, parse_visibility, visibility_allows

from .conftest import add_doc


def _vis(scope: str, **kwargs) -> str:
    return json.dumps({"scope": scope, **kwargs}, ensure_ascii=False)


def test_parse_visibility_defaults_to_all():
    assert parse_visibility(None)["scope"] == "all"
    assert parse_visibility("不是 json")["scope"] == "all"
    assert parse_visibility({"scope": "roles", "roles": ["hr"]})["scope"] == "roles"


def test_all_scope_allows_everyone():
    assert visibility_allows('{"scope":"all"}', User()) is True


def test_role_scope():
    vis = _vis("roles", roles=["hr"])
    assert visibility_allows(vis, User(roles=frozenset({"hr"}))) is True
    assert visibility_allows(vis, User(roles=frozenset({"employee"}))) is False


def test_dept_scope():
    vis = _vis("depts", depts=["finance"])
    assert visibility_allows(vis, User(dept="finance")) is True
    assert visibility_allows(vis, User(dept="rd")) is False


def test_user_scope():
    vis = _vis("users", users=["u-1"])
    assert visibility_allows(vis, User(id="u-1")) is True
    assert visibility_allows(vis, User(id="u-2")) is False


def test_expired_document_is_excluded(seeded, conn):
    add_doc(
        conn,
        seeded,
        "doc_old",
        "旧版制度",
        [("一、年假", "年假 3 天。")],
        effective_to="2020-01-01",
    )
    user = User()
    docs = allowed_documents(conn, user)
    assert all(row["id"] != "doc_old" for row in docs)
    ids = allowed_chunk_ids(conn, user)
    old_chunks = conn.execute("SELECT id FROM chunks WHERE doc_id='doc_old'").fetchall()
    assert old_chunks
    assert not ({row["id"] for row in old_chunks} & ids)


def test_restricted_doc_not_in_candidate_set(seeded, conn):
    employee_ids = allowed_chunk_ids(conn, User(roles=frozenset({"employee"})))
    salary_chunks = conn.execute("SELECT id FROM chunks WHERE doc_id='doc_salary'").fetchall()
    assert salary_chunks
    assert not ({row["id"] for row in salary_chunks} & employee_ids)

    hr_ids = allowed_chunk_ids(conn, User(roles=frozenset({"hr"})))
    assert {row["id"] for row in salary_chunks} <= hr_ids


def test_soft_deleted_doc_is_excluded(seeded, conn):
    conn.execute(
        "UPDATE documents SET deleted_at='2026-01-01T00:00:00', status='deleted' WHERE id='doc_travel'"
    )
    conn.commit()
    ids = allowed_chunk_ids(conn, User())
    travel = conn.execute("SELECT id FROM chunks WHERE doc_id='doc_travel'").fetchall()
    assert not ({row["id"] for row in travel} & ids)


def test_can_read_document_requires_ready_status(seeded, conn):
    row = conn.execute("SELECT * FROM documents WHERE id='doc_leave'").fetchone()
    assert can_read_document(row, User()) is True
    conn.execute("UPDATE documents SET status='failed' WHERE id='doc_leave'")
    row = conn.execute("SELECT * FROM documents WHERE id='doc_leave'").fetchone()
    assert can_read_document(row, User()) is False
