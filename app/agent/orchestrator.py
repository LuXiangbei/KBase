from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Iterator

from ..cache import CACHE, SemanticCache
from ..config import Settings, get_settings
from ..db import dumps, loads, new_id, now_iso
from ..ingest.parser import count_tokens
from ..providers import get_embedder, get_llm
from ..retrieval.filters import allowed_documents
from ..retrieval.service import RetrievalResult, retrieve
from ..security import User
from ..tracing import Trace, record as record_trace
from . import state_machine as sm
from .tools import ToolError, audit, create_ticket, get_ticket_status

REFUSAL_TEXT = (
    "这个问题在现有制度库里没有找到明确依据，我不做猜测。"
    "你可以补充具体条款名称或适用范围，我帮你再查；"
    "如果确实需要人工确认，我可以直接帮你创建一张工单。"
)

TICKET_HINT = "\n\n要转人工的话，直接说「帮我建个工单」并说明是哪类事项即可。"


@dataclass
class Turn:
    session_id: str
    message_id: str
    intent: str
    state: str
    citations: list[dict] = field(default_factory=list)
    insufficient: bool = False
    slots: dict = field(default_factory=dict)
    ticket: dict | None = None
    text: str = ""
    usage: dict = field(default_factory=dict)
    latency: dict = field(default_factory=dict)
    debug: dict = field(default_factory=dict)
    cached: bool = False
    cache_signature: str | None = None
    trace_id: str = ""
    cost_cny: float = 0.0
    _gen: Iterator[str] | None = None

    def stream(self) -> Iterator[str]:
        if self._gen is None:
            for i in range(0, len(self.text), 8):
                yield self.text[i : i + 8]
            return
        for piece in self._gen:
            self.text += piece
            yield piece

    def chunked(self) -> Iterator[str]:
        for i in range(0, len(self.text), 8):
            yield self.text[i : i + 8]


def _get_session(conn: sqlite3.Connection, session_id: str | None, user: User) -> sqlite3.Row:
    if session_id:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row:
            return row
    sid = session_id or new_id("ses")
    ts = now_iso()
    conn.execute(
        "INSERT INTO sessions (id,user_id,state,slots,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (sid, user.id, sm.STATE_RECEIVED, "{}", ts, ts),
    )
    conn.commit()
    return conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


def _save_message(
    conn: sqlite3.Connection, session_id: str, role: str, content: str, citations: list[dict] | None = None
) -> str:
    mid = new_id("msg")
    conn.execute(
        "INSERT INTO messages (id,session_id,role,content,citations,created_at) VALUES (?,?,?,?,?,?)",
        (mid, session_id, role, content, dumps(citations) if citations else None, now_iso()),
    )
    return mid


def _cache_signature(conn: sqlite3.Connection, user: User) -> str:
    docs = [(row["id"], row["version"]) for row in allowed_documents(conn, user)]
    return SemanticCache.signature(user.roles, user.dept, docs)


def estimate_cost(usage: dict, settings: Settings) -> float:
    return round(
        usage.get("in", 0) / 1000 * settings.price_input_per_1k
        + usage.get("out", 0) / 1000 * settings.price_output_per_1k,
        6,
    )


def _ticket_text(ticket: dict) -> str:
    if ticket.get("deduped"):
        return f"这张工单刚刚已经创建过了，编号 {ticket['id']}，不用重复提交。"
    return (
        f"已为你创建工单 {ticket['id']}（类别：{ticket['category']}，紧急度：{ticket['urgency']}）。"
        "处理进度可以在工单面板查看；同一请求重复提交不会产生新工单。"
    )


def build_turn(
    conn: sqlite3.Connection,
    user: User,
    message: str,
    session_id: str | None = None,
    request_id: str | None = None,
    settings: Settings | None = None,
) -> Turn:
    settings = settings or get_settings()
    started = time.perf_counter()
    session = _get_session(conn, session_id, user)
    slots = loads(session["slots"], {})
    _save_message(conn, session["id"], "user", message)
    conn.commit()  # 立刻落盘：流式生成的连接是另一条，不能长期占写锁

    intent = sm.route_intent(message)
    # 上一轮还在等槽位时，这一轮的补充信息要继续走建单，而不是掉回问答
    if session["state"] == sm.STATE_NEED_SLOT and intent in ("policy_qa", "out_of_scope"):
        intent = "ticket_create"
    turn = build_turn_for_intent(conn, user, message, session, slots, intent, request_id, settings)
    turn.latency["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return turn


def build_turn_for_intent(
    conn: sqlite3.Connection,
    user: User,
    message: str,
    session,
    slots: dict,
    intent: str,
    request_id: str | None,
    settings: Settings,
) -> Turn:
    turn = Turn(
        session_id=session["id"],
        message_id="",
        intent=intent,
        state=sm.STATE_ROUTED,
        slots=dict(slots),
    )

    blocked = sm.safety_block(message)
    if blocked:
        turn.intent = "out_of_scope"
        turn.state = sm.STATE_HANDOFF
        turn.insufficient = True
        turn.text = sm.SAFETY_MESSAGES[blocked]
        audit(conn, user, "chat.safety_block", {"code": blocked})
        conn.commit()
        return turn

    if intent == "chitchat":
        turn.state = sm.STATE_DONE
        turn.text = "你好，我是 KBase 制度助手。可以问我考勤、报销、合同、信息安全等制度条款，也可以直接说「帮我建单」转人工。"
        return turn

    if intent == "ticket_status":
        turn.state = sm.STATE_EXECUTING
        match = sm.TICKET_ID_RE.search(message)
        if not match:
            turn.state = sm.STATE_NEED_SLOT
            turn.text = "请把工单号发我（形如 tkt_xxxxxxxx），我帮你查进度。"
            return turn
        try:
            ticket = get_ticket_status(conn, user, match.group(0))
            turn.ticket = ticket
            turn.text = (
                f"工单 {ticket['id']} 当前状态：{ticket['status']}，"
                f"类别 {ticket['category']}，创建于 {ticket['created_at']}。"
            )
        except ToolError as exc:
            turn.text = f"查不到：{exc.message}"
        turn.state = sm.STATE_DONE
        return turn

    if intent == "ticket_create":
        agent = sm.next_agent_turn(intent, message, slots)
        turn.slots = agent.slots
        if agent.state == sm.STATE_NEED_SLOT:
            turn.state = sm.STATE_NEED_SLOT
            turn.text = f"好的，我来帮你建单。{agent.question}"
            return turn
        turn.state = sm.STATE_EXECUTING
        try:
            ticket = create_ticket(
                conn,
                user,
                request_id=request_id or new_id("req"),
                category=agent.slots.get("category", "other"),
                summary=agent.slots.get("summary", message[:80]),
                detail=agent.slots.get("detail", ""),
                urgency=agent.slots.get("urgency", "normal"),
            )
            turn.ticket = ticket
            turn.state = sm.STATE_DONE
            turn.text = _ticket_text(ticket)
            turn.usage = {"in": count_tokens(message), "out": count_tokens(turn.text)}
        except ToolError as exc:
            turn.state = sm.STATE_HANDOFF
            turn.text = f"建单没成功：{exc.message}"
        return turn

    # policy_qa / out_of_scope
    turn.cache_signature = _cache_signature(conn, user) if settings.cache_enabled else None
    if settings.cache_enabled and turn.cache_signature:
        qvec = get_embedder(settings.embed_dim).embed_one(message)
        cached = CACHE.get(qvec, turn.cache_signature)
        if cached:
            turn.state = sm.STATE_DONE
            turn.cached = True
            turn.text = cached.get("text", "")
            turn.citations = cached.get("citations", [])
            turn.usage = {"in": count_tokens(message), "out": count_tokens(turn.text)}
            turn.latency["retrieve_ms"] = 0.0
            return turn

    result: RetrievalResult = retrieve(conn, message, user, settings)
    turn.debug = result.debug
    turn.insufficient = result.insufficient
    turn.citations = result.citations_public
    turn.latency["retrieve_ms"] = result.debug.get("elapsed_ms", 0.0)

    if result.insufficient or not result.contexts:
        turn.state = sm.STATE_HANDOFF
        turn.text = REFUSAL_TEXT
        turn.usage = {"in": count_tokens(message), "out": count_tokens(turn.text)}
        return turn

    llm = get_llm()
    turn.state = sm.STATE_DONE
    turn._gen = llm.stream(message, result.contexts)
    turn.usage = {
        "in": count_tokens(message) + sum(count_tokens(c["text"]) for c in result.contexts),
        "out": 0,
    }
    return turn


def finalize_turn(conn: sqlite3.Connection, turn: Turn, user: User, message: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    turn.usage["out"] = count_tokens(turn.text)
    turn.latency.setdefault("llm_ms", 0.0)
    turn.cost_cny = estimate_cost(turn.usage, settings)
    # 生成侧拒答（检索到了但没有任何句子能支撑）也要按"证据不足"处理
    if turn.text.startswith("这个问题在现有制度库里没有找到明确依据"):
        turn.insufficient = True
        turn.citations = []
    # 只保留真的被回答引用到的条款，避免“检索到但没用到”的文档冒充依据
    if turn.citations and turn.text:
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", turn.text)}
        turn.citations = [c for c in turn.citations if c["n"] in cited]
    # 成功的问答才进语义缓存；拒答、建单结果不缓存
    if (
        settings.cache_enabled
        and not turn.cached
        and turn.text
        and not turn.insufficient
        and turn.intent in ("policy_qa",)
        and turn.cache_signature
    ):
        qvec = get_embedder(settings.embed_dim).embed_one(message)
        CACHE.put(
            qvec,
            turn.cache_signature,
            {"text": turn.text, "citations": turn.citations},
        )
    if turn.text:
        turn.message_id = _save_message(conn, turn.session_id, "assistant", turn.text, turn.citations)
    conn.execute(
        "UPDATE sessions SET state=?, slots=?, updated_at=? WHERE id=?",
        (turn.state, dumps(turn.slots), now_iso(), turn.session_id),
    )
    audit(
        conn,
        user,
        "chat.turn",
        {
            "intent": turn.intent,
            "state": turn.state,
            "insufficient": turn.insufficient,
            "citations": len(turn.citations),
            "chars": len(turn.text),
            "cached": turn.cached,
            "cost_cny": turn.cost_cny,
        },
    )
    conn.commit()

    turn.trace_id = new_id("tr")
    record_trace(
        Trace(
            trace_id=turn.trace_id,
            ts=time.time(),
            user_id=user.id,
            intent=turn.intent,
            state=turn.state,
            question=message,
            retrieval=turn.debug,
            citations=len(turn.citations),
            answer_chars=len(turn.text),
            usage=turn.usage,
            latency_ms=turn.latency,
            cost_cny=turn.cost_cny,
            cached=turn.cached,
            ticket_id=(turn.ticket or {}).get("id"),
        )
    )
    return {
        "session_id": turn.session_id,
        "message_id": turn.message_id,
        "intent": turn.intent,
        "state": turn.state,
        "insufficient": turn.insufficient,
        "citations": turn.citations,
        "ticket": turn.ticket,
        "usage": turn.usage,
        "cached": turn.cached,
        "trace_id": turn.trace_id,
        "cost_cny": turn.cost_cny,
        "latency_ms": turn.latency,
        "debug": turn.debug,
    }
