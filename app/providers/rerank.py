from __future__ import annotations

import re
from typing import Sequence

import numpy as np

from .embed_hash import content_features, iter_features

SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_SPLIT.split(text) if p.strip()]
    return [p for p in parts if len(p) >= 2]


def _features_set(text: str) -> set[str]:
    return set(iter_features(text))


def coverage(query_feats: set[str], text: str) -> float:
    if not query_feats:
        return 0.0
    text_feats = _features_set(text)
    if not text_feats:
        return 0.0
    hit = len(query_feats & text_feats)
    return hit / len(query_feats)


class HeuristicReranker:
    """0.6 * 向量余弦 + 0.3 * 词面覆盖 + 0.1 * 路径命中，输出归一到 [0, 1]。"""

    name = "heuristic"
    w_vec = 0.6
    w_cov = 0.3
    w_path = 0.1

    def score(
        self,
        query: str,
        query_vec: np.ndarray,
        candidates: Sequence[dict],
        alias_query: str | None = None,
    ) -> list[float]:
        q_feats = set(content_features(query))
        alias_feats = set(content_features(alias_query)) if alias_query else set()
        scores: list[float] = []
        for cand in candidates:
            vec = cand.get("vec")
            cos = float(np.dot(query_vec, vec)) if vec is not None else 0.0
            cos = max(0.0, min(1.0, cos))
            text = cand.get("text", "")
            cov = coverage(q_feats, text)
            if alias_feats:
                # 同义词扩展只用来补召回，不能稀释原查询的精确匹配
                cov = max(cov, coverage(alias_feats, text))
            path_hit = 1.0 if coverage(q_feats, cand.get("path", "")) > 0.2 else 0.0
            raw = self.w_vec * cos + self.w_cov * cov + self.w_path * path_hit
            scores.append(round(max(0.0, min(1.0, raw)), 6))
        return scores
