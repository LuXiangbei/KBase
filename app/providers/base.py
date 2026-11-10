from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """返回 shape=(n, dim) 的 float32 矩阵，行向量已 L2 归一化。"""


class LLMProvider(Protocol):
    name: str

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str: ...

    def stream(self, question: str, contexts: list[dict], history: list[dict] | None = None): ...


class Reranker(Protocol):
    def score(self, query: str, query_vec: np.ndarray, candidates: list[dict]) -> list[float]: ...
