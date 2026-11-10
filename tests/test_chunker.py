from __future__ import annotations

from app.ingest.chunker import build_chunks, split_long
from app.ingest.parser import Section, count_tokens


def test_split_long_respects_budget_and_overlap():
    text = "。".join([f"第{i}条 员工应当遵守公司的各项规章制度并按时完成工作" for i in range(12)]) + "。"
    chunks = split_long(text, max_tokens=40, overlap=0.2)
    assert len(chunks) > 1
    for chunk in chunks:
        assert count_tokens(chunk) <= 45
    joined = "".join(chunks)
    assert "第0条" in joined and "第11条" in joined


def test_split_long_leaves_short_text_untouched():
    assert split_long("很短的一句话。", 100, 0.15) == ["很短的一句话。"]


def test_split_long_handles_oversized_sentence():
    huge = "甲" * 200
    chunks = split_long(huge, 40, 0.15)
    assert len(chunks) > 1
    assert "".join(chunks) == huge


def test_build_chunks_keeps_structure_and_parent():
    sections = [
        Section(path="员工手册/第4章/4.2", text="年休假 5 天。", page=3),
        Section(path="员工手册/第4章/4.3", text="病假需要证明。", page=3),
    ]
    parents, chunks = build_chunks("doc1", sections, visibility="{}", doc_version=2)
    assert len(parents) == 2
    assert len(chunks) == 2
    assert chunks[0].path == "员工手册/第4章/4.2"
    assert chunks[0].parent_id == parents[0].id
    assert chunks[0].doc_version == 2
    assert chunks[1].page == 3


def test_build_chunks_splits_long_section():
    long_text = "。".join(["本条规定了员工在出差期间应当遵守的报销标准和审批流程"] * 20) + "。"
    sections = [Section(path="差旅/一", text=long_text)]
    parents, chunks = build_chunks("doc2", sections, visibility="{}", max_tokens=60)
    assert len(chunks) > 1
    assert all(c.parent_id == parents[0].id for c in chunks)
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_table_section_is_not_split():
    table = "\n".join(["城市 | 标准", "北京 | 800 元", "上海 | 800 元", "杭州 | 500 元"])
    sections = [Section(path="差旅/表格", text=table, is_table=True)]
    _, chunks = build_chunks("doc3", sections, visibility="{}", max_tokens=20)
    assert len(chunks) == 1
    assert "北京" in chunks[0].text


def test_token_count_is_stable():
    assert count_tokens("年假 5 天") == 4  # 年假天 + 数字 5
    assert count_tokens("annual leave") == 2


def test_chunk_ids_are_deterministic():
    sections = [Section(path="A", text="同样的内容。")]
    _, first = build_chunks("docX", sections, visibility="{}")
    _, second = build_chunks("docX", sections, visibility="{}")
    assert first[0].id == second[0].id
