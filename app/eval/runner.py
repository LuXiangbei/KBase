from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..agent.state_machine import safety_block
from ..config import Settings
from ..ingest.parser import count_tokens
from ..providers import get_llm
from ..retrieval.service import RetrievalResult, retrieve
from ..security import User

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.jsonl"

THRESHOLDS = {
    "recall_at_5": 0.85,
    "citation_valid_rate": 1.0,
    "refusal_accuracy": 0.85,
    "false_refusal_rate": 0.10,
}


def load_golden(path: Path | None = None) -> list[dict]:
    target = path or GOLDEN_PATH
    items = []
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _user(item: dict) -> User:
    roles = frozenset(item.get("roles") or ["employee"])
    return User(id=item.get("user_id") or "demo-user", roles=roles, dept=item.get("dept") or "general")


def _matched(item: dict, contexts: list[dict]) -> tuple[bool, int]:
    expected_docs = set(item.get("expected_docs") or [])
    expected_chunks = set(item.get("expected_chunk_ids") or [])
    for rank, ctx in enumerate(contexts[:5], start=1):
        if ctx.get("doc_id") in expected_docs or ctx.get("chunk_id") in expected_chunks:
            return True, rank
    return False, 0


def evaluate_item(conn: sqlite3.Connection, item: dict, settings: Settings) -> dict:
    user = _user(item)
    blocked = safety_block(item["question"])
    if blocked:
        result = RetrievalResult(insufficient=True, debug={"blocked": blocked})
    else:
        result = retrieve(conn, item["question"], user, settings)
    hit, rank = _matched(item, result.contexts)
    should_refuse = bool(item.get("should_refuse"))
    refused = result.insufficient or not result.contexts

    answer = ""
    if not refused:
        answer = get_llm().answer(item["question"], result.contexts)

    cites = result.citations_public
    citation_valid = all(c.get("n") and c.get("doc_title") and c.get("path") for c in cites)
    if not refused and not cites:
        citation_valid = False

    keywords = item.get("keywords") or []
    keyword_hit = all(k in answer for k in keywords) if keywords else True

    return {
        "id": item.get("id"),
        "type": item.get("type", "single"),
        "difficulty": item.get("difficulty", "normal"),
        "question": item["question"],
        "hit": hit,
        "rank": rank,
        "refused": refused,
        "should_refuse": should_refuse,
        "refusal_ok": refused == should_refuse,
        "citation_valid": citation_valid,
        "keyword_hit": keyword_hit,
        "top_score": result.top_score,
        "answer": answer[:160],
        "debug": result.debug,
    }


def summarize(details: list[dict]) -> dict:
    normal = [d for d in details if d.get("difficulty") != "hard"]
    hard = [d for d in details if d.get("difficulty") == "hard"]
    answerable = [d for d in normal if not d["should_refuse"]]
    refusal_items = [d for d in normal if d["should_refuse"]]
    hard_answerable = [d for d in hard if not d["should_refuse"]]
    total = len(details) or 1

    recall = sum(1 for d in answerable if d["hit"]) / (len(answerable) or 1)
    mrr = sum((1.0 / d["rank"]) for d in answerable if d["hit"]) / (len(answerable) or 1)
    citation_valid = sum(1 for d in details if d["citation_valid"]) / total
    refusal_acc = sum(1 for d in refusal_items if d["refused"]) / (len(refusal_items) or 1)
    false_refusal = sum(1 for d in answerable if d["refused"]) / (len(answerable) or 1)
    keyword_hit = sum(1 for d in answerable if d["keyword_hit"]) / (len(answerable) or 1)
    hard_recall = (
        sum(1 for d in hard_answerable if d["hit"]) / (len(hard_answerable) or 1)
        if hard_answerable
        else None
    )

    metrics = {
        "items": len(details),
        "normal_items": len(normal),
        "hard_items": len(hard),
        "recall_at_5": round(recall, 4),
        "mrr": round(mrr, 4),
        "citation_valid_rate": round(citation_valid, 4),
        "refusal_accuracy": round(refusal_acc, 4),
        "false_refusal_rate": round(false_refusal, 4),
        "keyword_hit_rate": round(keyword_hit, 4),
        "hard_recall_at_5": None if hard_recall is None else round(hard_recall, 4),
    }
    checks = {
        "recall_at_5": metrics["recall_at_5"] >= THRESHOLDS["recall_at_5"],
        "citation_valid_rate": metrics["citation_valid_rate"] >= THRESHOLDS["citation_valid_rate"],
        "refusal_accuracy": metrics["refusal_accuracy"] >= THRESHOLDS["refusal_accuracy"],
        "false_refusal_rate": metrics["false_refusal_rate"] <= THRESHOLDS["false_refusal_rate"],
    }
    metrics["passed"] = all(checks.values())
    metrics["checks"] = checks
    metrics["thresholds"] = THRESHOLDS
    return metrics


def run_evaluation(
    conn: sqlite3.Connection,
    settings: Settings,
    items: list[dict] | None = None,
    golden_path: Path | None = None,
) -> dict:
    items = items if items is not None else load_golden(golden_path)
    details = [evaluate_item(conn, item, settings) for item in items]
    report = summarize(details)
    report["failures"] = [
        {"id": d["id"], "question": d["question"], "why": "未召回" if not d["hit"] else "拒答判断错"}
        for d in details
        if d.get("difficulty") != "hard"
        and ((not d["hit"] and not d["should_refuse"]) or not d["refusal_ok"])
    ][:20]
    report["hard_failures"] = [
        {"id": d["id"], "question": d["question"], "why": "未召回" if not d["hit"] else "拒答判断错"}
        for d in details
        if d.get("difficulty") == "hard" and (not d["hit"] or not d["refusal_ok"])
    ][:20]
    report["details"] = details
    report["usage"] = {"tokens_in": sum(count_tokens(i["question"]) for i in items)}
    return report
