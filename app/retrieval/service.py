from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..providers import get_embedder, get_reranker
from ..providers.embed_hash import iter_features
from ..providers.rerank import coverage
from ..retrieval import hybrid
from ..retrieval.filters import allowed_chunk_ids
from ..retrieval.index import get_index
from ..retrieval.query import expand_query
from ..security import User, contains_injection


@dataclass
class RetrievalResult:
    contexts: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    insufficient: bool = True
    top_score: float = 0.0
    debug: dict = field(default_factory=dict)

    @property
    def citations_public(self) -> list[dict]:
        if self.insufficient:
            # 证据不足时不展示引用：避免用户把“相关文档”误当成“答案依据”
            return []
        return [
            {"n": c["n"], "doc_title": c["doc_title"], "path": c["path"], "page": c["page"], "snippet": c["snippet"]}
            for c in self.citations
        ]


def _load_parents(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT * FROM parents WHERE id IN ({placeholders})", ids).fetchall()
    return {row["id"]: {"text": row["text"], "path": row["path"], "page": row["page"]} for row in rows}


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    user: User,
    settings: Settings | None = None,
    top_k: int | None = None,
) -> RetrievalResult:
    settings = settings or get_settings()
    started = time.perf_counter()

    embedder = get_embedder(settings.embed_dim)
    index = get_index(settings.embed_dim)
    if not index.loaded:
        index.reload(settings.db_path)

    allowed = allowed_chunk_ids(conn, user)
    if not allowed:
        return RetrievalResult(insufficient=True, debug={"allowed": 0})

    qvec = embedder.embed_one(query)
    expanded = expand_query(query)
    bm25_hits = index.search_bm25(conn, expanded, allowed, k=settings.retrieve_top_k)
    vec_hits = index.search_vector(qvec, allowed, k=settings.retrieve_top_k)

    bm25_ids = [cid for cid, _ in bm25_hits]
    vec_ids = [cid for cid, _ in vec_hits]
    fused = hybrid.rrf([bm25_ids, vec_ids], k=settings.rrf_k)
    ordered = hybrid.dedupe_by_parent([cid for cid, _ in fused], index.meta)
    # 间接注入防护：含指令型文本的 chunk 不进入候选（不是生成后再过滤）
    before = len(ordered)
    ordered = [cid for cid in ordered if not contains_injection(index.meta[cid]["text"])]
    quarantined = before - len(ordered)

    reranker = get_reranker()
    candidates = [
        {
            "id": cid,
            "text": index.meta[cid]["text"],
            "path": index.meta[cid]["path"],
            "vec": index.vec_of(cid),
        }
        for cid in ordered
    ]
    scores = reranker.score(query, qvec, candidates, alias_query=expanded)
    ranked = sorted(zip(ordered, scores), key=lambda kv: (-kv[1], kv[0]))
    limit = top_k or settings.rerank_top_k
    selected = [cid for cid, _ in ranked[:limit]]
    top_score = ranked[0][1] if ranked else 0.0

    insufficient = top_score < settings.refusal_threshold or (
        not bm25_ids and top_score < settings.refusal_weak
    )

    parent_ids = [index.meta[cid]["parent_id"] for cid in selected if cid in index.meta]
    parents = _load_parents(conn, parent_ids)
    contexts = hybrid.assemble_contexts(selected, index.meta, parents)

    # 词面证据：在前 3 条上下文里"并集"覆盖了多少查询特征。
    # 多跳问题需要跨文档拼证据，用并集而不是单块最大值，避免把合法的多跳问句误拒。
    top_texts = [c["text"] for c in contexts[:5]]
    q_feats = set(iter_features(query))
    q_feats_expanded = set(iter_features(expanded))
    covered = set()
    covered_expanded = set()
    for text in top_texts:
        covered |= q_feats & set(iter_features(text))
        covered_expanded |= q_feats_expanded & set(iter_features(text))
    cov_original = len(covered) / (len(q_feats) or 1)
    cov_expanded = len(covered_expanded) / (len(q_feats_expanded) or 1)
    max_cov = max(cov_original, cov_expanded)
    if max_cov < settings.refusal_coverage:
        insufficient = True

    debug = {
        "allowed": len(allowed),
        "bm25": len(bm25_ids),
        "vector": len(vec_ids),
        "fused": len(fused),
        "quarantined": quarantined,
        "top_score": round(top_score, 4),
        "max_coverage": round(max_cov, 4),
        "coverage_original": round(cov_original, 4),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return RetrievalResult(
        contexts=contexts,
        citations=contexts,
        insufficient=insufficient,
        top_score=top_score,
        debug=debug,
    )
