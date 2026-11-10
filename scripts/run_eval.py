"""跑黄金集评测，打印指标并按阈值返回退出码。

用法：
    python scripts/run_eval.py            # 人读格式
    python scripts/run_eval.py --json     # 机器读格式
    python scripts/run_eval.py --write-report docs/eval-report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 评测必须可复现且不烧钱：强制走离线 provider，不受 .env 里的在线模型影响
os.environ["KBASE_LLM_PROVIDER"] = "local"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import db_session, init_db  # noqa: E402
from app.eval.runner import run_evaluation  # noqa: E402
from app.retrieval.index import get_index  # noqa: E402


def render_markdown(report: dict, providers: dict) -> str:
    m = report
    rows = [
        ("核心集 Recall@5", m["recall_at_5"], ">= 0.85"),
        ("难样本 Recall@5（改写型）", m.get("hard_recall_at_5"), "观测项"),
        ("MRR", m["mrr"], "-"),
        ("引用有效率", m["citation_valid_rate"], "= 1.00"),
        ("拒答准确率", m["refusal_accuracy"], ">= 0.85"),
        ("误拒率", m["false_refusal_rate"], "<= 0.10"),
        ("关键词命中率", m["keyword_hit_rate"], "-"),
    ]
    lines = [
        "# KBase 评测报告（本机实测）",
        "",
        f"- 黄金集：{m['items']} 条（核心 {m['normal_items']} + 难样本 {m['hard_items']}）",
        f"- 生成时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Provider：embedding={providers.get('embedding')}, llm={providers.get('llm')}, rerank={providers.get('rerank')}",
        f"- 结论：{'核心集达标' if m['passed'] else '未达标'}",
        "",
        "| 指标 | 实测 | 阈值 |",
        "|---|---|---|",
    ]
    for name, value, threshold in rows:
        lines.append(f"| {name} | {'—' if value is None else value} | {threshold} |")
    lines += [
        "",
        "## 核心集未通过项",
        "",
    ]
    if m["failures"]:
        for f in m["failures"]:
            lines.append(f"- `{f['id']}` {f['question']} —— {f['why']}")
    else:
        lines.append("- 无")
    lines += ["", "## 难样本未通过项（不计入门禁）", ""]
    if m.get("hard_failures"):
        for f in m["hard_failures"]:
            lines.append(f"- `{f['id']}` {f['question']} —— {f['why']}")
    else:
        lines.append("- 无")
    lines += [
        "",
        "> 说明：本报告的 Recall@5 按「标准文档是否出现在前 5 条召回」计算；引用有效率为引用条目是否都带文档名与条款路径；"
        "拒答准确率只统计应拒答样本；误拒率只统计可回答样本。所有数字由 `scripts/run_eval.py` 在本机真实跑出。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--golden", type=str, default=None)
    parser.add_argument("--write-report", type=str, default=None)
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    init_db(settings.db_path)
    get_index(settings.embed_dim).reload(settings.db_path)

    with db_session(settings.db_path) as conn:
        report = run_evaluation(
            conn, settings, golden_path=Path(args.golden) if args.golden else None
        )

    from app.providers import describe_providers

    providers = describe_providers(settings)

    if args.verbose:
        for d in report["details"]:
            flag = "OK " if d["hit"] or d["should_refuse"] else "MISS"
            print(f"{flag} {d['id']} {d['question'][:40]} top={d['top_score']} ref={d['refused']}")

    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "details"}, ensure_ascii=False, indent=2))
    else:
        print("=== KBase 评测 ===")
        print(f"样本：{report['items']}  通过：{report['passed']}")
        for key in ("recall_at_5", "mrr", "citation_valid_rate", "refusal_accuracy", "false_refusal_rate", "keyword_hit_rate"):
            print(f"  {key:22s} {report[key]}")
        if report.get("hard_recall_at_5") is not None:
            print(f"  {'hard_recall_at_5':22s} {report['hard_recall_at_5']}  (改写型难样本，观测项)")
        if report["failures"]:
            print("未通过：")
            for f in report["failures"]:
                print(f"  - {f['id']} {f['question'][:36]} ({f['why']})")

    if args.write_report:
        target = ROOT / args.write_report
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(report, providers), encoding="utf-8")
        print(f"report written: {target}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
