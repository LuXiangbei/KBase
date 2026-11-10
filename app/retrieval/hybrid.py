from __future__ import annotations

from typing import Iterable, Sequence


def rrf(
    rank_lists: Sequence[Sequence[str]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：只用名次，不用分数，免归一化。"""
    if weights is None:
        weights = [1.0] * len(rank_lists)
    scores: dict[str, float] = {}
    for weight, ids in zip(weights, rank_lists):
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def dedupe_by_parent(
    ordered: Iterable[str], meta: dict[str, dict]
) -> list[str]:
    """同一父块只保留排名最高的一条，避免上下文被同一段话占满。"""
    seen: set[str] = set()
    out: list[str] = []
    for cid in ordered:
        info = meta.get(cid)
        if not info:
            continue
        key = info.get("parent_id") or cid
        if key in seen:
            continue
        seen.add(key)
        out.append(cid)
    return out


def assemble_contexts(
    selected: Sequence[str],
    meta: dict[str, dict],
    parents: dict[str, dict],
    max_chars: int = 900,
) -> list[dict]:
    """父块回填：命中子块，给模型父块全文（截断），引用保留子块页码。"""
    contexts = []
    for n, cid in enumerate(selected, start=1):
        info = meta[cid]
        parent = parents.get(info.get("parent_id") or "")
        text = (parent or {}).get("text") or info["text"]
        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        contexts.append(
            {
                "n": n,
                "chunk_id": cid,
                "doc_id": info["doc_id"],
                "doc_title": info["doc_title"],
                "path": info["path"],
                "page": info["page"],
                "text": text,
                "snippet": info["text"][:160],
            }
        )
    return contexts
