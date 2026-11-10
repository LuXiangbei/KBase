from __future__ import annotations

from app.retrieval.hybrid import dedupe_by_parent, rrf


def test_rrf_orders_by_fused_rank():
    fused = rrf([["a", "b", "c"], ["a"]], k=60)
    assert [doc for doc, _ in fused][:2] == ["a", "b"]
    assert fused[0][1] > fused[-1][1]


def test_rrf_ties_are_deterministic():
    # 两路名次完全对称时按 id 稳定排序，避免线上结果抖动
    assert [doc for doc, _ in rrf([["a", "b"], ["b", "a"]], k=60)] == ["a", "b"]


def test_rrf_k_changes_head_dominance():
    lists = [["a", "b"], ["a", "b"]]
    small = dict(rrf(lists, k=1))
    large = dict(rrf(lists, k=100))
    assert (small["a"] - small["b"]) > (large["a"] - large["b"])


def test_rrf_weights_can_shift_order():
    fused = dict(rrf([["a"], ["b"]], k=60, weights=[1.0, 3.0]))
    assert fused["b"] > fused["a"]


def test_rrf_handles_empty_and_ties():
    assert rrf([[], []]) == []
    fused = rrf([["x"], ["x"]], k=60)
    assert fused[0][0] == "x"
    assert len(fused) == 1


def test_dedupe_by_parent_keeps_first():
    meta = {
        "c1": {"parent_id": "p1"},
        "c2": {"parent_id": "p1"},
        "c3": {"parent_id": "p2"},
    }
    assert dedupe_by_parent(["c1", "c2", "c3"], meta) == ["c1", "c3"]


def test_dedupe_ignores_unknown_ids():
    meta = {"c1": {"parent_id": "p1"}}
    assert dedupe_by_parent(["nope", "c1"], meta) == ["c1"]
