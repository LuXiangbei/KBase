from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings, reset_settings_cache  # noqa: E402
from app.cache import CACHE  # noqa: E402
from app.db import db_session, init_db  # noqa: E402
from app.ingest import pipeline  # noqa: E402
from app.retrieval.index import reset_index  # noqa: E402
from app.tracing import clear as clear_traces  # noqa: E402


def _clear_provider_caches() -> None:
    from app.providers import get_embedder, get_llm, get_reranker

    get_embedder.cache_clear()
    get_llm.cache_clear()
    get_reranker.cache_clear()


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("KBASE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KBASE_LLM_PROVIDER", "local")
    reset_settings_cache()
    reset_index()
    CACHE.clear()
    clear_traces()
    _clear_provider_caches()
    s = get_settings()
    s.ensure_dirs()
    init_db(s.db_path)
    yield s
    reset_index()


@pytest.fixture()
def conn(settings):
    with db_session(settings.db_path) as c:
        yield c


@pytest.fixture()
def client(settings):
    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def add_doc(
    conn,
    settings,
    doc_id: str,
    title: str,
    sections: list[tuple[str, str]],
    visibility: dict | None = None,
    effective_to: str | None = None,
) -> dict:
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines += [f"## {heading}", "", body, ""]
    data = "\n".join(lines).encode("utf-8")
    pipeline.ingest_upload(
        conn,
        f"{doc_id}.md",
        data,
        settings,
        title=title,
        visibility=visibility,
        effective_to=effective_to,
        doc_id=doc_id,
    )
    return pipeline.index_document(conn, doc_id, settings)


@pytest.fixture()
def seeded(conn, settings):
    add_doc(
        conn,
        settings,
        "doc_leave",
        "休假制度",
        [
            ("第1条 年假", "工龄满 1 年不满 10 年的，年休假 5 天；满 10 年的，年休假 10 天。"),
            ("第2条 病假", "病假需提供二级以上医院证明，全年累计不超过 30 天。"),
        ],
    )
    add_doc(
        conn,
        settings,
        "doc_travel",
        "差旅标准",
        [
            ("一、住宿", "一线城市住宿标准为 800 元/晚，二线城市 500 元/晚。"),
            ("二、交通", "飞机舱位标准为经济舱，高铁二等座。"),
        ],
    )
    add_doc(
        conn,
        settings,
        "doc_salary",
        "薪酬管理办法",
        [("一、结构", "月度薪酬由基本工资 70% 与绩效工资 30% 构成。")],
        visibility={"scope": "roles", "roles": ["hr"]},
    )
    return settings


def md_bytes(title: str, sections: list[tuple[str, str]]) -> bytes:
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines += [f"## {heading}", "", body, ""]
    return "\n".join(lines).encode("utf-8")


def upload(client, filename: str, title: str, sections: list[tuple[str, str]], **form):
    files = {"file": (filename, io.BytesIO(md_bytes(title, sections)), "text/markdown")}
    data = {"title": title, **{k: str(v) for k, v in form.items()}}
    return client.post("/api/docs", files=files, data=data)
