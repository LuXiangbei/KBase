from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..cache import CACHE
from ..config import Settings, get_settings
from ..db import db_session
from ..deps import ConnDep, SettingsDep, UserDep
from ..ingest import pipeline
from ..retrieval.index import get_index
from ..security import User

router = APIRouter(tags=["docs"])


def _index_background(doc_id: str, settings: Settings) -> None:
    with db_session(settings.db_path) as conn:
        pipeline.index_document(conn, doc_id, settings)


@router.get("/docs")
def list_docs(conn: sqlite3.Connection = ConnDep) -> dict:
    rows = conn.execute(
        """
        SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id) AS chunk_count
        FROM documents d WHERE d.deleted_at IS NULL ORDER BY d.created_at DESC
        """
    ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "error": row["error"],
                "version": row["version"],
                "visibility": json.loads(row["visibility"]),
                "effective_to": row["effective_to"],
                "chunks": row["chunk_count"],
                "created_at": row["created_at"],
                "mime": row["mime"],
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/docs", status_code=202)
async def upload_doc(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    visibility: str | None = Form(default=None),
    effective_to: str | None = Form(default=None),
    conn: sqlite3.Connection = ConnDep,
    settings: Settings = SettingsDep,
    user: User = UserDep,
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 20MB")
    vis = None
    if visibility:
        try:
            vis = json.loads(visibility)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"visibility 不是合法 JSON: {exc}") from exc
    try:
        result = pipeline.ingest_upload(
            conn, file.filename or "upload.md", data, settings, title=title, visibility=vis, effective_to=effective_to
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not result.get("deduped"):
        background.add_task(_index_background, result["doc_id"], settings)
    return JSONResponse(status_code=202, content=result)


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str, conn: sqlite3.Connection = ConnDep, settings: Settings = SettingsDep) -> dict:
    row = conn.execute("SELECT id FROM documents WHERE id=? AND deleted_at IS NULL", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    pipeline.soft_delete(conn, doc_id)
    get_index(settings.embed_dim).reload(settings.db_path)
    CACHE.clear()
    return {"doc_id": doc_id, "status": "deleted"}


@router.post("/docs/{doc_id}/reindex")
def reindex_doc(
    doc_id: str,
    background: BackgroundTasks,
    conn: sqlite3.Connection = ConnDep,
    settings: Settings = SettingsDep,
) -> dict:
    row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    background.add_task(_index_background, doc_id, settings)
    return {"doc_id": doc_id, "status": "pending"}


@router.get("/docs/{doc_id}/chunks")
def doc_chunks(doc_id: str, conn: sqlite3.Connection = ConnDep) -> dict:
    rows = conn.execute(
        "SELECT id,parent_id,ord,path,page,tokens,substr(text,1,200) AS preview FROM chunks WHERE doc_id=? ORDER BY ord",
        (doc_id,),
    ).fetchall()
    return {"doc_id": doc_id, "items": [dict(row) for row in rows], "total": len(rows)}


@router.post("/docs/{doc_id}/purge")
def purge_doc(doc_id: str, conn: sqlite3.Connection = ConnDep, settings: Settings = SettingsDep) -> dict:
    pipeline.purge_document(conn, doc_id)
    get_index(settings.embed_dim).reload(settings.db_path)
    CACHE.clear()
    return {"doc_id": doc_id, "status": "purged"}


@router.get("/docs/{doc_id}/file")
def doc_file(doc_id: str, conn: sqlite3.Connection = ConnDep):
    row = conn.execute("SELECT source_path, title FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = Path(row["source_path"])
    if not path.exists():
        raise HTTPException(status_code=410, detail="原件已删除")
    from fastapi.responses import FileResponse

    return FileResponse(str(path), filename=path.name)
