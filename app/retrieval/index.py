from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import numpy as np

from ..db import db_session, loads
from ..providers.embed_hash import iter_features


class SearchIndex:
    """内存向量矩阵 + FTS5 倒排。数据量在百万 chunk 以内时够用，再往上应换专用向量库。"""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.lock = threading.RLock()
        self.ids: list[str] = []
        self.mat: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self.pos: dict[str, int] = {}
        self.meta: dict[str, dict] = {}
        self.loaded = False

    def clear(self) -> None:
        with self.lock:
            self.ids = []
            self.mat = np.zeros((0, self.dim), dtype=np.float32)
            self.pos = {}
            self.meta = {}
            self.loaded = False

    def reload(self, db_path: Path | str) -> int:
        with db_session(db_path) as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.doc_id, c.parent_id, c.ord, c.path, c.page, c.text, c.tokens,
                       c.visibility, c.doc_version,
                       d.title AS doc_title, d.status AS doc_status, d.deleted_at,
                       d.effective_to, d.visibility AS doc_visibility
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                ORDER BY c.doc_id, c.ord
                """
            ).fetchall()
            emb_rows = conn.execute("SELECT chunk_id, vec FROM embeddings").fetchall()

        vectors = {row["chunk_id"]: np.frombuffer(row["vec"], dtype=np.float32) for row in emb_rows}
        ids: list[str] = []
        meta: dict[str, dict] = {}
        matrix = []
        for row in rows:
            vec = vectors.get(row["id"])
            if vec is None or vec.shape[0] != self.dim:
                continue
            ids.append(row["id"])
            meta[row["id"]] = {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "parent_id": row["parent_id"],
                "ord": row["ord"],
                "path": row["path"] or "",
                "page": row["page"] or 1,
                "text": row["text"],
                "tokens": row["tokens"],
                "doc_title": row["doc_title"],
                "doc_version": row["doc_version"],
            }
            matrix.append(vec)

        with self.lock:
            self.ids = ids
            self.pos = {cid: i for i, cid in enumerate(ids)}
            self.meta = meta
            self.mat = np.vstack(matrix).astype(np.float32) if matrix else np.zeros((0, self.dim), dtype=np.float32)
            self.loaded = True
        return len(ids)

    def __len__(self) -> int:
        return len(self.ids)

    def vec_of(self, chunk_id: str) -> np.ndarray | None:
        i = self.pos.get(chunk_id)
        return None if i is None else self.mat[i]

    def search_vector(self, query_vec: np.ndarray, allowed: set[str], k: int = 50) -> list[tuple[str, float]]:
        with self.lock:
            if not self.ids or not allowed:
                return []
            positions = [self.pos[cid] for cid in allowed if cid in self.pos]
            if not positions:
                return []
            sub = self.mat[positions]
            scores = sub @ query_vec.astype(np.float32)
            take = min(k, len(positions))
            top = np.argpartition(-scores, take - 1)[:take]
            top = top[np.argsort(-scores[top])]
            return [(self.ids[positions[i]], float(scores[i])) for i in top]

    def search_bm25(
        self, conn: sqlite3.Connection, query: str, allowed: set[str], k: int = 50
    ) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(iter_features(query)))[:32]
        if not terms or not allowed:
            return []
        match = " OR ".join(f'"{t}"' for t in terms if '"' not in t)
        if not match:
            return []
        try:
            rows = conn.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS rank FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, max(k * 4, 64)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[tuple[str, float]] = []
        for row in rows:
            cid = row["chunk_id"]
            if cid in allowed:
                out.append((cid, -float(row["rank"])))
                if len(out) >= k:
                    break
        return out

    def doc_count(self) -> int:
        return len({m["doc_id"] for m in self.meta.values()})


_INDEX: SearchIndex | None = None


def get_index(dim: int | None = None) -> SearchIndex:
    global _INDEX
    if _INDEX is None:
        from ..config import get_settings

        _INDEX = SearchIndex(dim or get_settings().embed_dim)
    return _INDEX


def reset_index() -> None:
    global _INDEX
    if _INDEX is not None:
        _INDEX.clear()
    _INDEX = None
