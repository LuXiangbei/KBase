from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
ASCII_WORD = re.compile(r"[a-zA-Z0-9_]+")
HEADING_MD = re.compile(r"^(#{1,6})\s+(.*)$")


def count_tokens(text: str) -> int:
    """粗略 token 估算：中文按字、英文按词、数字串按 3 位一档。工程上用目标模型分词器替换。"""
    cjk = len(CJK.findall(text))
    words = len(ASCII_WORD.findall(text))
    return cjk + words


@dataclass
class Section:
    path: str
    text: str
    page: int = 1
    is_table: bool = False


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _push(stack: list[str], level: int, title: str) -> None:
    while len(stack) >= level:
        stack.pop()
    stack.append(title.strip())


def parse_markdown(text: str, fallback_title: str) -> list[Section]:
    sections: list[Section] = []
    stack: list[str] = [fallback_title]
    buf: list[str] = []

    def flush() -> None:
        body = _clean("\n".join(buf))
        if body:
            sections.append(Section(path="/".join(stack), text=body))
        buf.clear()

    for line in text.splitlines():
        m = HEADING_MD.match(line)
        if m:
            flush()
            _push(stack, len(m.group(1)), m.group(2))
            continue
        buf.append(line)
    flush()
    if not sections:
        body = _clean(text)
        if body:
            sections.append(Section(path=fallback_title, text=body))
    return sections


def parse_docx(path: Path, fallback_title: str) -> list[Section]:
    from docx import Document

    doc = Document(str(path))
    sections: list[Section] = []
    stack: list[str] = [fallback_title]
    buf: list[str] = []

    def flush() -> None:
        body = _clean("\n".join(buf))
        if body:
            sections.append(Section(path="/".join(stack), text=body))
        buf.clear()

    for para in doc.paragraphs:
        style = (para.style.name or "").lower()
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("heading") or style.startswith("标题"):
            level = 1
            digits = re.findall(r"\d+", style)
            if digits:
                level = max(1, min(6, int(digits[0])))
            flush()
            _push(stack, level, text)
            continue
        buf.append(text)

    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            flush()
            sections.append(Section(path="/".join(stack) + "/表格", text="\n".join(rows), is_table=True))
    flush()
    return sections


def parse_pdf(path: Path, fallback_title: str) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    sections: list[Section] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = _clean(page.extract_text() or "")
        if text:
            sections.append(Section(path=fallback_title, text=text, page=idx))
    return sections


def parse_file(path: Path, title: str | None = None) -> list[Section]:
    suffix = path.suffix.lower()
    title = title or path.stem
    if suffix in {".md", ".markdown"}:
        return parse_markdown(path.read_text(encoding="utf-8", errors="replace"), title)
    if suffix == ".txt":
        body = _clean(path.read_text(encoding="utf-8", errors="replace"))
        return [Section(path=title, text=body)] if body else []
    if suffix == ".docx":
        return parse_docx(path, title)
    if suffix == ".pdf":
        return parse_pdf(path, title)
    raise ValueError(f"不支持的文档类型: {suffix}")
