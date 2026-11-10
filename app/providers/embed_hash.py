from __future__ import annotations

import hashlib
import re
from typing import Iterable, Sequence

import numpy as np

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
ASCII_TOKEN = re.compile(r"[a-zA-Z0-9_]+")


def iter_features(text: str) -> Iterable[str]:
    """中文二元组 + 英文/数字词。查询与文档共用，保证同一向量空间。"""
    for run in CJK_RUN.findall(text):
        if len(run) == 1:
            yield run
            continue
        for i in range(len(run) - 1):
            yield run[i : i + 2]
    for tok in ASCII_TOKEN.findall(text.lower()):
        yield tok


# 口语问句里大量“我/了/的/该/谁”这类虚词，会让覆盖率分母虚高，算证据前先去掉。
STOP_CHARS = set("的了吗呢吧啊我你他她它能要该谁呀嘛哈就都也还和与及是在有把被让给得想会")


def content_features(text: str) -> list[str]:
    return [f for f in iter_features(text) if not any(ch in STOP_CHARS for ch in f)]


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, (1.0 if (value >> 63) & 1 == 0 else -1.0)


class HashEmbedder:
    """确定性离线嵌入：signed hashing，无外部依赖、可复现。

    它不是语义模型，只提供词面相似度信号；真实语义检索请切换到 OpenAI 兼容嵌入。
    """

    name = "hash"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            vec = out[row]
            for feature in iter_features(text):
                idx, sign = _bucket(feature, self.dim)
                vec[idx] += sign
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
        return out

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]
