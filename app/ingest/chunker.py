from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import Section, count_tokens

SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")


@dataclass
class Chunk:
    id: str
    doc_id: str
    parent_id: str
    ord: int
    path: str
    page: int
    text: str
    tokens: int
    visibility: str
    doc_version: int


@dataclass
class Parent:
    id: str
    doc_id: str
    path: str
    page: int
    text: str
    tokens: int
    visibility: str
    doc_version: int


def _sentences(text: str) -> list[str]:
    return [s for s in SENT_SPLIT.split(text) if s.strip()]


def _truncate(text: str, max_tokens: int) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    out, used = [], 0
    for sent in _sentences(text):
        t = count_tokens(sent)
        if used + t > max_tokens:
            break
        out.append(sent)
        used += t
    return "".join(out) if out else text[: max_tokens * 2]


def split_long(text: str, max_tokens: int, overlap: float) -> list[str]:
    """按句子滑窗切分，重叠约 overlap；单句超长时按字符兜底。"""
    if count_tokens(text) <= max_tokens:
        return [text]
    sentences = _sentences(text)
    try:
        overlap_tokens = int(max_tokens * overlap)
    except TypeError:
        overlap_tokens = 0
    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for sent in sentences:
        t = count_tokens(sent)
        if t > max_tokens:
            if cur:
                chunks.append("".join(cur))
                cur, cur_tokens = [], 0
            step = max(1, max_tokens // 2)
            for i in range(0, len(sent), step):
                chunks.append(sent[i : i + step])
            continue
        if cur and cur_tokens + t > max_tokens:
            chunks.append("".join(cur))
            tail: list[str] = []
            tail_tokens = 0
            for prev in reversed(cur):
                pt = count_tokens(prev)
                if tail_tokens + pt > overlap_tokens:
                    break
                tail.insert(0, prev)
                tail_tokens += pt
            cur, cur_tokens = tail, tail_tokens
        cur.append(sent)
        cur_tokens += t
    if cur:
        chunks.append("".join(cur))
    return [c for c in chunks if c.strip()]


def build_chunks(
    doc_id: str,
    sections: list[Section],
    visibility: str,
    doc_version: int = 1,
    max_tokens: int = 420,
    overlap: float = 0.15,
    parent_max_tokens: int = 1800,
) -> tuple[list[Parent], list[Chunk]]:
    parents: list[Parent] = []
    chunks: list[Chunk] = []
    for s_idx, section in enumerate(sections):
        parent_id = f"{doc_id}:p{s_idx}"
        parents.append(
            Parent(
                id=parent_id,
                doc_id=doc_id,
                path=section.path,
                page=section.page,
                text=_truncate(section.text, parent_max_tokens),
                tokens=count_tokens(section.text),
                visibility=visibility,
                doc_version=doc_version,
            )
        )
        parts = [section.text] if section.is_table else split_long(section.text, max_tokens, overlap)
        for c_idx, part in enumerate(parts):
            chunks.append(
                Chunk(
                    id=f"{doc_id}:c{s_idx}:{c_idx}",
                    doc_id=doc_id,
                    parent_id=parent_id,
                    ord=c_idx,
                    path=section.path,
                    page=section.page,
                    text=part,
                    tokens=count_tokens(part),
                    visibility=visibility,
                    doc_version=doc_version,
                )
            )
    return parents, chunks


def chunk_section_texts(
    doc_id: str,
    texts: list[tuple[str, str]],
    visibility: str,
    **kwargs,
) -> tuple[list[Parent], list[Chunk]]:
    sections = [Section(path=p, text=t) for p, t in texts]
    return build_chunks(doc_id, sections, visibility, **kwargs)
