from __future__ import annotations

from typing import Sequence

import numpy as np


class OpenAICompatEmbedder:
    """OpenAI 兼容 /embeddings。切换后必须重建索引（向量空间会变）。"""

    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 512):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": list(texts)},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vectors = np.array([item["embedding"] for item in data], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms
