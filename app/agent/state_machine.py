from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS = ("policy_qa", "ticket_create", "ticket_status", "chitchat", "out_of_scope")

TICKET_CREATE_WORDS = ("建单", "工单", "报修", "报销", "申请", "投诉", "转人工", "找人工", "人工处理")
TICKET_STATUS_WORDS = ("工单状态", "进度", "到哪了", "处理到", "查工单")
CHITCHAT_WORDS = ("你好", "您好", "谢谢", "在吗", "hi", "hello", "早上好", "下午好")

CATEGORY_RULES = (
    ("hr", ("年假", "请假", "考勤", "薪酬", "工资", "社保", "公积金", "入职", "离职", "调岗", "报销")),
    ("legal", ("合同", "条款", "违约", "保密", "法务", "诉讼", "知识产权")),
    ("it", ("账号", "vpn", "邮箱", "电脑", "系统", "权限", "网络", "密码")),
)

URGENT_WORDS = ("紧急", "马上", "立刻", "尽快", "很急")
LOW_WORDS = ("不急", "有空", "随便")

TICKET_ID_RE = re.compile(r"tkt_[0-9a-f]{8,}")

SLOT_QUESTIONS = {
    "category": "这是哪一类事项？人事、法务、IT，还是其他？",
    "summary": "能用一句话说明你要办的事吗？",
}

SAFETY_RULES = (
    (re.compile(r"系统提示词|system\s*prompt|开发者模式|内部指令|你的指令", re.I), "PROMPT_EXTRACTION"),
    (re.compile(r"绕过|规避|破解|跳过.{0,6}审批|不用审批|直接把章"), "BYPASS_APPROVAL"),
    (re.compile(r"(竞争对手|友商|竞品).{0,8}(报价|价格|薪资|工资|待遇)"), "OUT_OF_DOMAIN"),
)

SAFETY_MESSAGES = {
    "PROMPT_EXTRACTION": "我不能输出系统提示词或内部指令。如果你在查制度条款，我可以继续帮你检索；也可以帮你转人工。",
    "BYPASS_APPROVAL": "这类请求涉及绕过公司审批或用印流程，我不能提供规避办法。紧急业务可以走加急审批，或我帮你建单转人工。",
    "OUT_OF_DOMAIN": "竞争对手或外部市场的价格属于库外信息，我不能凭空给数字。内部采购比价规则可以查，需要的话我也可以帮你转人工。",
}


def safety_block(text: str) -> str | None:
    """命中即拒答：提示词套取与流程绕过不交给检索/生成。"""
    for pattern, code in SAFETY_RULES:
        if pattern.search(text or ""):
            return code
    return None

STATE_RECEIVED = "RECEIVED"
STATE_ROUTED = "ROUTED"
STATE_NEED_SLOT = "NEED_SLOT"
STATE_READY = "READY"
STATE_EXECUTING = "EXECUTING"
STATE_CONFIRMING = "CONFIRMING"
STATE_DONE = "DONE"
STATE_HANDOFF = "HANDOFF"


@dataclass
class AgentTurn:
    intent: str
    state: str
    slots: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    question: str = ""
    ticket: dict | None = None
    tool_calls: int = 0


def route_intent(text: str) -> str:
    raw = (text or "").strip()
    low = raw.lower()
    if not raw:
        return "chitchat"
    if TICKET_ID_RE.search(raw) or any(w in raw for w in TICKET_STATUS_WORDS):
        return "ticket_status"
    if any(w in raw for w in TICKET_CREATE_WORDS):
        return "ticket_create"
    if any(w in low for w in CHITCHAT_WORDS) and len(raw) <= 12:
        return "chitchat"
    return "policy_qa"


def detect_category(text: str) -> str | None:
    low = text.lower()
    for category, words in CATEGORY_RULES:
        if any(w in low for w in words):
            return category
    return None


def extract_slots(text: str, existing: dict) -> dict:
    slots = dict(existing or {})
    category = detect_category(text)
    if category:
        slots["category"] = category
    if not slots.get("summary"):
        cleaned = re.sub(r"^(你好|您好|麻烦|请|帮我|我想|我要)+", "", text).strip()
        if cleaned:
            slots["summary"] = cleaned[:80]
    slots["detail"] = (slots.get("detail") or "") + ("\n" if slots.get("detail") else "") + text[:500]
    if any(w in text for w in URGENT_WORDS):
        slots["urgency"] = "high"
    elif any(w in text for w in LOW_WORDS):
        slots["urgency"] = "low"
    else:
        slots.setdefault("urgency", "normal")
    return slots


def next_agent_turn(intent: str, text: str, slots: dict) -> AgentTurn:
    if intent != "ticket_create":
        return AgentTurn(intent=intent, state=STATE_ROUTED, slots=dict(slots or {}))

    merged = extract_slots(text, slots)
    missing = [key for key in ("category", "summary") if not merged.get(key)]
    if missing:
        first = missing[0]
        return AgentTurn(
            intent=intent,
            state=STATE_NEED_SLOT,
            slots=merged,
            missing=missing,
            question=SLOT_QUESTIONS.get(first, f"请补充 {first}"),
        )
    return AgentTurn(intent=intent, state=STATE_READY, slots=merged)
