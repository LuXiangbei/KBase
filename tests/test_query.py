from __future__ import annotations

from app.providers.embed_hash import content_features
from app.retrieval.query import expand_query, normalize_query


def test_normalize_colloquial_amounts_and_leave():
    assert "工龄 8 年" in normalize_query("我干了八年，能歇多少天假？")
    assert "住宿" in normalize_query("住酒店的钱最多能报多少？")
    assert "年假" in normalize_query("一年能请几天假？")


def test_normalize_approval_and_departure():
    assert "审批" in normalize_query("请假找谁批？")
    assert "离职" in normalize_query("我想走，得提前多久说？")
    assert "通知" in normalize_query("我想走，得提前多久说？")


def test_normalize_numbers():
    assert "5 万" in normalize_query("五万以上谁签字？")


def test_expand_query_keeps_original_and_adds_synonyms():
    expanded = expand_query("年假几天？")
    assert "年假" in expanded
    assert "年休假" in expanded


def test_content_features_drops_stopword_bigrams():
    feats = set(content_features("我想走，得提前多久说？"))
    assert "我想" not in feats
    assert "得提" not in feats
    assert "离职" in set(content_features("我想走，得提前多久说？")) or "多久" in feats


def test_content_features_keeps_domain_terms():
    feats = set(content_features("离职后多久内要禁用账号？"))
    assert {"离职", "账号", "禁用"} <= feats
