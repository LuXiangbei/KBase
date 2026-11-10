from __future__ import annotations

import re

COMPLEX_HINTS = (
    "还剩", "剩余", "还剩几天", "计算", "一共", "合计", "减去", "加上", "扣掉",
    "分别", "对比", "同时", "并且", "如果", "假设", "为什么", "怎么理解", "是否",
)
LEAVE_WORDS = ("年假", "年休假", "调休", "请假", "休假")


def is_complex(question: str, history: list[dict] | None = None) -> bool:
    text = question.strip()
    if len(text) >= 28:
        return True
    if any(hint in text for hint in COMPLEX_HINTS):
        return True
    # “工龄 10 年 + 已休 3 天”这类需要做减法的问法
    if re.search(r"\d", text) and any(word in text for word in LEAVE_WORDS):
        return True
    if re.search(r"(工龄|干了|入职).{0,4}(\d+|[一二三四五六七八九十]+)\s*年", text):
        return True
    if history and any(word in text for word in LEAVE_WORDS) and len(history) >= 2:
        return True
    return False


def route_mode(question: str, history: list[dict] | None = None) -> str:
    return "complex" if is_complex(question, history) else "simple"
