"""端到端演示：对运行中的服务跑一遍完整流程。

用法：
    python scripts/e2e_demo.py --base http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

DOCS = [
    (
        "demo_leave.md",
        "休假制度（演示）",
        "# 休假制度（演示）\n\n## 第1条 年假\n\n工龄满 1 年不满 10 年的，年休假 5 天；满 10 年的，年休假 10 天。\n\n## 第2条 病假\n\n病假需提供二级以上医院证明，全年累计不超过 30 天。\n",
    ),
    (
        "demo_travel.md",
        "差旅标准（演示）",
        "# 差旅标准（演示）\n\n## 一、住宿\n\n一线城市住宿标准为 800 元/晚，二线城市 500 元/晚。\n\n## 二、交通\n\n飞机舱位标准为经济舱，高铁二等座。\n",
    ),
    (
        "demo_contract.md",
        "合同管理办法（演示）",
        "# 合同管理办法（演示）\n\n## 一、审批\n\n合同金额 100 万元以上需总经理审批，100 万元以下由分管副总审批。\n",
    ),
]


def sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            events.append((name, json.loads("\n".join(data))))
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    failures: list[str] = []

    with httpx.Client(base_url=base, timeout=30.0) as client:
        health = client.get("/api/health")
        print(f"[health] {health.status_code} {health.json()['status']} chunks={health.json()['index_chunks']}")
        if health.status_code != 200:
            return 1

        created = []
        for filename, title, body in DOCS:
            res = client.post(
                "/api/docs",
                files={"file": (filename, body.encode("utf-8"), "text/markdown")},
                data={"title": title},
            )
            payload = res.json()
            created.append((payload.get("doc_id"), payload.get("status")))
            print(f"[upload] {title} -> {res.status_code} {payload}")
            if res.status_code not in (200, 202):
                failures.append(f"upload {title}")

        listing = client.get("/api/docs").json()
        ready = [d for d in listing["items"] if d["status"] == "ready"]
        print(f"[docs] total={listing['total']} ready={len(ready)}")
        if len(ready) < 3:
            failures.append("docs not ready")

        # 流式问答
        with client.stream("POST", "/api/chat/stream", json={"message": "一线城市住宿标准是多少？"}) as resp:
            raw = "".join(resp.iter_text())
        events = sse_events(raw)
        names = [n for n, _ in events]
        answer = "".join(p["text"] for n, p in events if n == "delta")
        meta = next((p for n, p in events if n == "meta"), {})
        print(f"[sse] events={names[:3]}…{names[-1]} deltas={names.count('delta')}")
        print(f"[answer] {answer[:120]}")
        print(f"[citations] {[c['doc_title'] for c in meta.get('citations', [])]}")
        if "800" not in answer:
            failures.append("answer missing 800")
        if not meta.get("citations"):
            failures.append("no citations in meta")

        # 拒答
        refused = client.post("/api/chat", json={"message": "公司股票代码是多少？"}).json()
        print(f"[refusal] insufficient={refused['insufficient']} citations={len(refused['citations'])}")
        if not refused["insufficient"] or refused["citations"]:
            failures.append("refusal failed")

        # 工单 + 幂等
        payload = {"message": "帮我建个工单：报销发票丢了，很急", "request_id": "demo-req-1"}
        first = client.post("/api/chat", json=payload).json()
        second = client.post("/api/chat", json=payload).json()
        ticket = first.get("ticket") or {}
        print(f"[ticket] {ticket.get('id')} category={ticket.get('category')} dedup={second.get('ticket', {}).get('deduped')}")
        if not ticket.get("id"):
            failures.append("ticket not created")
        elif second.get("ticket", {}).get("id") != ticket.get("id"):
            failures.append("ticket not idempotent")

        metrics = client.get("/api/metrics").json()
        print(f"[metrics] requests={metrics['requests']} p95={metrics['latency_p95_ms']}ms")

        # 清理演示文档，避免污染种子语料的检索排序（同时验证 DELETE 接口）
        for doc_id, status in created:
            if status == "pending":
                res = client.delete(f"/api/docs/{doc_id}")
                print(f"[cleanup] {doc_id} -> {res.status_code}")

    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
