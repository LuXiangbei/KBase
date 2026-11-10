from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from ..cache import CACHE
from ..config import Settings, get_settings
from ..db import dumps, new_id, now_iso
from ..providers import get_embedder
from ..providers.embed_hash import iter_features
from ..retrieval.index import get_index
from ..security import parse_visibility
from .chunker import build_chunks
from .parser import parse_file

MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


def _bigram_text(text: str) -> str:
    return " ".join(iter_features(text))


def ingest_upload(
    conn: sqlite3.Connection,
    filename: str,
    data: bytes,
    settings: Settings | None = None,
    title: str | None = None,
    visibility: dict | None = None,
    effective_to: str | None = None,
    doc_id: str | None = None,
) -> dict:
    settings = settings or get_settings()
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_BY_SUFFIX:
        raise ValueError(f"不支持的文档类型: {suffix}")
    content_hash = hashlib.sha256(data).hexdigest()

    existing = conn.execute(
        "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing and not existing["deleted_at"]:
        return {"doc_id": existing["id"], "status": existing["status"], "deduped": True}

    doc_id = existing["id"] if existing else (doc_id or new_id("doc"))
    settings.ensure_dirs()
    target = settings.upload_dir / f"{doc_id}{suffix}"
    target.write_bytes(data)

    vis = dumps(visibility or {"scope": "all"})
    ts = now_iso()
    if existing:
        conn.execute(
            "UPDATE documents SET status='pending', error=NULL, deleted_at=NULL, version=version+1, "
            "updated_at=?, visibility=?, effective_to=?, source_path=?, title=COALESCE(?, title) WHERE id=?",
            (ts, vis, effective_to, str(target), title, doc_id),
        )
    else:
        conn.execute(
            "INSERT INTO documents (id,title,source_path,mime,content_hash,version,effective_from,"
            "effective_to,visibility,status,error,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,?,?,1,NULL,?,?,'pending',NULL,?,?,NULL)",
            (doc_id, title or Path(filename).stem, str(target), MIME_BY_SUFFIX[suffix], content_hash,
             effective_to, vis, ts, ts),
        )
    conn.commit()
    return {"doc_id": doc_id, "status": "pending", "deduped": False}


def index_document(conn: sqlite3.Connection, doc_id: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        raise KeyError(doc_id)

    conn.execute("UPDATE documents SET status='indexing', updated_at=? WHERE id=?", (now_iso(), doc_id))
    conn.commit()

    try:
        path = Path(doc["source_path"])
        sections = parse_file(path, doc["title"])
        if not sections:
            raise ValueError("解析结果为空（可能是扫描件或加密 PDF）")
        parents, chunks = build_chunks(
            doc_id,
            sections,
            visibility=doc["visibility"],
            doc_version=doc["version"],
            max_tokens=settings.chunk_max_tokens,
            overlap=settings.chunk_overlap,
            parent_max_tokens=settings.parent_max_tokens,
        )
        if not chunks:
            raise ValueError("切分结果为空")

        conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,)
        )
        conn.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM parents WHERE doc_id=?", (doc_id,))

        conn.executemany(
            "INSERT INTO parents (id,doc_id,path,page,text,tokens,visibility,doc_version) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(p.id, p.doc_id, p.path, p.page, p.text, p.tokens, p.visibility, p.doc_version) for p in parents],
        )
        conn.executemany(
            "INSERT INTO chunks (id,doc_id,parent_id,ord,path,page,text,tokens,visibility,doc_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (c.id, c.doc_id, c.parent_id, c.ord, c.path, c.page, c.text, c.tokens,
                 c.visibility, c.doc_version)
                for c in chunks
            ],
        )
        conn.executemany(
            "INSERT INTO chunks_fts (chunk_id, bigram_text) VALUES (?, ?)",
            [(c.id, _bigram_text(c.text)) for c in chunks],
        )

        embedder = get_embedder(settings.embed_dim)
        vectors = embedder.embed([c.text for c in chunks])
        conn.executemany(
            "INSERT OR REPLACE INTO embeddings (chunk_id, dim, vec) VALUES (?,?,?)",
            [(c.id, settings.embed_dim, vectors[i].astype("float32").tobytes()) for i, c in enumerate(chunks)],
        )
        conn.execute(
            "UPDATE documents SET status='ready', error=NULL, updated_at=? WHERE id=?",
            (now_iso(), doc_id),
        )
        conn.commit()
        get_index(settings.embed_dim).reload(settings.db_path)
        CACHE.clear()
        return {
            "doc_id": doc_id,
            "status": "ready",
            "chunks": len(chunks),
            "parents": len(parents),
        }
    except Exception as exc:  # noqa: BLE001 - 失败要落到文档状态里，便于前端展示
        conn.rollback()
        conn.execute(
            "UPDATE documents SET status='failed', error=?, updated_at=? WHERE id=?",
            (f"{type(exc).__name__}: {exc}"[:500], now_iso(), doc_id),
        )
        conn.commit()
        return {"doc_id": doc_id, "status": "failed", "error": str(exc)}


def soft_delete(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute(
        "UPDATE documents SET deleted_at=?, status='deleted', updated_at=? WHERE id=?",
        (now_iso(), now_iso(), doc_id),
    )
    conn.commit()


def purge_document(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
    conn.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM parents WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
