from __future__ import annotations

import json

from .conftest import upload


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "providers" in body


def test_upload_list_and_chunks(client):
    res = upload(
        client,
        "leave.md",
        "休假制度",
        [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")],
    )
    assert res.status_code == 202
    doc_id = res.json()["doc_id"]

    listing = client.get("/api/docs").json()
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "ready"
    assert listing["items"][0]["chunks"] >= 1

    chunks = client.get(f"/api/docs/{doc_id}/chunks").json()
    assert chunks["total"] >= 1
    assert "年休假" in chunks["items"][0]["preview"]


def test_upload_is_deduplicated(client):
    payload = [("第1条 年假", "年休假 5 天。")]
    first = upload(client, "a.md", "休假制度", payload).json()
    second = upload(client, "a.md", "休假制度", payload).json()
    assert first["doc_id"] == second["doc_id"]
    assert second["deduped"] is True


def test_upload_rejects_unknown_type(client):
    res = client.post(
        "/api/docs",
        files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
        data={"title": "bad"},
    )
    assert res.status_code == 400


def test_chat_returns_answer_with_citations(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")])
    res = client.post("/api/chat", json={"message": "年假有几天？"})
    assert res.status_code == 200
    body = res.json()
    assert body["text"]
    assert body["citations"]
    assert body["citations"][0]["doc_title"] == "休假制度"
    assert body["insufficient"] is False


def test_chat_refuses_out_of_scope(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "年休假 5 天。")])
    body = client.post("/api/chat", json={"message": "明天天气怎么样？"}).json()
    assert body["insufficient"] is True
    assert "没有找到" in body["text"] or "不知道" in body["text"]


def test_chat_stream_event_sequence(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")])
    res = client.post("/api/chat/stream", json={"message": "年假有几天？"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "delta" in names
    text = "".join(payload["text"] for name, payload in events if name == "delta")
    assert text
    done = events[-1][1]
    assert done["citations"]
    assert done["usage"]["out"] > 0


def test_chat_stream_keeps_session(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "年休假 5 天。")])
    first = parse_sse(client.post("/api/chat/stream", json={"message": "你好"}).text)
    session_id = first[0][1]["session_id"]
    second = parse_sse(
        client.post("/api/chat/stream", json={"message": "年假几天？", "session_id": session_id}).text
    )
    assert second[0][1]["session_id"] == session_id


def test_ticket_api_is_idempotent(client):
    payload = {"category": "hr", "summary": "报销发票丢了", "request_id": "req-abc", "urgency": "high"}
    first = client.post("/api/tickets", json=payload).json()
    second = client.post("/api/tickets", json=payload).json()
    assert first["id"] == second["id"]
    assert second["deduped"] is True
    listing = client.get("/api/tickets").json()
    assert listing["total"] == 1


def test_ticket_api_validates_payload(client):
    res = client.post("/api/tickets", json={"category": "nope", "summary": "x", "request_id": "req-1"})
    assert res.status_code == 400


def test_metrics_endpoint(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "年休假 5 天。")])
    client.post("/api/chat", json={"message": "年假有几天？"})
    body = client.get("/api/metrics").json()
    assert body["requests"] >= 1
    assert "latency_p95_ms" in body
    assert body["index_chunks"] >= 1


def test_acl_blocks_salary_document_for_employee(client):
    upload(
        client,
        "salary.md",
        "薪酬管理办法",
        [("一、结构", "月度薪酬由基本工资 70% 与绩效工资 30% 构成。")],
        visibility=json.dumps({"scope": "roles", "roles": ["hr"]}),
    )
    body = client.post("/api/chat", json={"message": "薪酬结构是什么？"}).json()
    assert all(c["doc_title"] != "薪酬管理办法" for c in body["citations"])

    hr_body = client.post(
        "/api/chat",
        json={"message": "薪酬结构是什么？"},
        headers={"X-User-Id": "hr-1", "X-User-Roles": "hr"},
    ).json()
    assert any(c["doc_title"] == "薪酬管理办法" for c in hr_body["citations"])


def test_static_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "KBase" in res.text


def test_semantic_cache_hit(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")])
    first = client.post("/api/chat", json={"message": "年假有几天？"}).json()
    second = client.post("/api/chat", json={"message": "年假有几天？"}).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["text"] == first["text"]
    assert second["citations"] == first["citations"]
    stats = client.get("/api/cache").json()
    assert stats["hits"] >= 1


def test_cache_invalidated_after_new_document(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")])
    client.post("/api/chat", json={"message": "年假有几天？"})
    upload(client, "travel.md", "差旅标准", [("一、住宿", "一线城市住宿标准为 800 元/晚。")])
    later = client.post("/api/chat", json={"message": "年假有几天？"}).json()
    assert later["cached"] is False


def test_trace_recorded_and_queryable(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "工龄满 1 年的员工年休假 5 天。")])
    done = client.post("/api/chat", json={"message": "年假有几天？"}).json()
    trace_id = done["trace_id"]
    assert trace_id.startswith("tr_")
    trace = client.get(f"/api/traces/{trace_id}").json()
    assert trace["question"] == "年假有几天？"
    assert trace["citations"] >= 1
    assert trace["latency_ms"]
    listing = client.get("/api/traces").json()
    assert listing["total"] >= 1
    assert client.get("/api/traces/tr_missing").status_code == 404


def test_cost_is_zero_for_local_provider(client):
    upload(client, "leave.md", "休假制度", [("第1条 年假", "年休假 5 天。")])
    done = client.post("/api/chat", json={"message": "年假有几天？"}).json()
    assert done["cost_cny"] == 0
    assert done["usage"]["in"] > 0
    assert done["usage"]["out"] > 0
    assert done["mode"] in ("simple", "complex")


def test_tools_endpoint_lists_whitelist(client):
    body = client.get("/api/tools").json()
    assert set(body["whitelist"]) == {"search_policy", "get_ticket_status", "create_ticket", "handoff"}
    assert body["items"]["create_ticket"]["idempotent"] is True
