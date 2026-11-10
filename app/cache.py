from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class CacheEntry:
    vec: np.ndarray
    signature: str
    payload: dict
    expires_at: float


class SemanticCache:
    """相似问题复用答案。

    三条硬约束（也是面试里最容易被追问的点）：
    1. 命中要带"文档版本 + 用户可见范围"签名，文档更新或权限变化后自然失效；
    2. 只缓存成功的问答，拒答与办事结果不进缓存；
    3. 相似度阈值偏高（默认 0.95），宁可少命中，不可答非所问。
    """

    def __init__(self, threshold: float = 0.95, ttl_seconds: int = 1800, max_items: int = 256):
        self.threshold = threshold
        self.ttl = ttl_seconds
        self._items: deque[CacheEntry] = deque(maxlen=max_items)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def signature(user_roles: frozenset[str], dept: str, docs: list[tuple[str, int]]) -> str:
        raw = "|".join([",".join(sorted(user_roles)), dept] + [f"{d}:{v}" for d, v in sorted(docs)])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def get(self, vec: np.ndarray, signature: str) -> dict | None:
        now = time.time()
        best: tuple[float, dict] | None = None
        with self._lock:
            for entry in self._items:
                if entry.expires_at < now or entry.signature != signature:
                    continue
                score = float(np.dot(vec, entry.vec))
                if score >= self.threshold and (best is None or score > best[0]):
                    best = (score, entry.payload)
            if best is None:
                self.misses += 1
                return None
            payload = best[1]
            if str(payload.get("text", "")).startswith("这个问题在现有制度库里没有找到明确依据"):
                self.misses += 1
                return None
            self.hits += 1
            return {**payload, "cache_score": round(best[0], 4)}

    def put(self, vec: np.ndarray, signature: str, payload: dict) -> None:
        with self._lock:
            self._items.append(
                CacheEntry(vec=vec.astype(np.float32), signature=signature, payload=payload,
                           expires_at=time.time() + self.ttl)
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "items": len(self._items),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "threshold": self.threshold,
            "ttl_seconds": self.ttl,
        }


CACHE = SemanticCache()
