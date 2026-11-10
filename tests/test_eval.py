from __future__ import annotations

from pathlib import Path

from app.eval.runner import load_golden, run_evaluation, summarize


def _detail(hit=True, refused=False, should_refuse=False, citation=True, keyword=True, rank=1):
    return {
        "id": "x",
        "hit": hit,
        "rank": rank,
        "refused": refused,
        "should_refuse": should_refuse,
        "refusal_ok": refused == should_refuse,
        "citation_valid": citation,
        "keyword_hit": keyword,
    }


def test_summarize_perfect_scores():
    details = [_detail(), _detail(rank=2), _detail(should_refuse=True, refused=True)]
    metrics = summarize(details)
    assert metrics["recall_at_5"] == 1.0
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["false_refusal_rate"] == 0.0
    assert metrics["passed"] is True


def test_summarize_detects_regression():
    details = [_detail(hit=False), _detail(hit=False), _detail(should_refuse=True, refused=False)]
    metrics = summarize(details)
    assert metrics["recall_at_5"] == 0.0
    assert metrics["passed"] is False
    assert metrics["checks"]["recall_at_5"] is False


def test_golden_set_shape():
    items = load_golden()
    assert len(items) >= 60
    assert sum(1 for i in items if i.get("should_refuse")) >= 8
    assert sum(1 for i in items if i.get("type") == "multi") >= 10
    ids = {i["id"] for i in items}
    assert len(ids) == len(items)


def test_run_evaluation_on_subset(seeded, conn):
    items = [
        {"id": "t1", "question": "年假有几天？", "expected_docs": ["doc_leave"]},
        {"id": "t2", "question": "住宿标准是多少？", "expected_docs": ["doc_travel"]},
        {"id": "t3", "question": "明天天气怎么样？", "should_refuse": True, "expected_docs": []},
    ]
    report = run_evaluation(conn, seeded, items=items)
    assert report["items"] == 3
    assert report["recall_at_5"] == 1.0
    assert report["refusal_accuracy"] == 1.0
    assert report["passed"] is True
    assert len(report["details"]) == 3


def test_golden_file_exists_on_disk():
    path = Path(__file__).resolve().parents[1] / "app" / "eval" / "golden.jsonl"
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) >= 60
