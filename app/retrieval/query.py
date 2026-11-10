from __future__ import annotations

import re

# 领域同义词：用户说“年假”，制度写“年休假”。离线基线没有语义模型，靠术语归一补这一层。
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("年假", "年休假"),
    ("调休", "补休"),
    ("补休", "调休"),
    ("工资", "薪酬"),
    ("餐补", "餐费补贴", "出差补贴"),
    ("住宿", "住宿标准", "酒店"),
    ("打车", "市内交通", "交通费"),
    ("报销", "费用报销"),
    ("发票", "票据"),
    ("审批", "审批权限", "审批流程"),
    ("用印", "盖章"),
    ("合同", "合约"),
    ("超支", "超出预算"),
    ("账号", "账户"),
    ("密码", "口令"),
    ("设备", "电脑", "终端"),
    ("入职", "报到"),
    ("转正", "转正评估"),
    ("离职", "解除劳动合同", "交接"),
    ("加班", "补休"),
    ("出差", "差旅"),
    ("培训", "学习"),
    ("盘点", "清点"),
    ("招待", "宴请"),
    ("保密", "保密义务"),
    ("模板", "范本"),
    ("预算", "费用预算"),
)

_MAP: dict[str, set[str]] = {}
for group in SYNONYM_GROUPS:
    for term in group:
        _MAP.setdefault(term, set()).update(t for t in group if t != term)

# 口语 → 制度用语。顺序有意义，长的模式先匹配。
COLLOQUIAL_RULES: tuple[tuple[str, str], ...] = (
    (r"干(?:了)?([一二三四五六七八九十两\d]+)\s*年", lambda m: f"工龄 {_cn_to_int(m.group(1))} 年"),
    (r"([一二三四五六七八九十两\d]+)\s*万", lambda m: f"{_cn_to_int(m.group(1))} 万"),
    (r"([一二三四五六七八九十两\d]+)\s*千", lambda m: f"{_cn_to_int(m.group(1))} 千"),
    (r"住酒店|酒店", "住宿"),
    (r"找谁批|谁批|找谁审批|找谁签字", "审批"),
    (r"多久说|什么时候说", "多久通知"),
    (r"最多能报|报销多少|报多少", "报销标准"),
    (r"能报", "报销"),
    (r"多少钱", "费用标准"),
    (r"钱", "费用"),
    (r"歇", "休假"),
    (r"补休", "调休"),
    (r"想走|要走|辞职", "离职"),
    (r"找谁|跟谁说|向谁投诉", "投诉渠道"),
    (r"谁签字|谁审批", "审批"),
    (r"签字", "审批"),
    (r"要啥|需要啥", "需要什么"),
    (r"啥", "什么"),
    (r"咋", "如何"),
    (r"多长时间", "期限"),
    (r"几天假|请几天假", "年假"),
    (r"请假", "休假"),
)

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(raw: str) -> str:
    raw = raw.strip()
    if raw.isdigit():
        return raw
    if raw in _CN_DIGITS:
        return str(_CN_DIGITS[raw])
    if raw.startswith("十") and len(raw) == 1:
        return "10"
    return raw


def normalize_query(query: str) -> str:
    """把口语化问法归一成制度用语，再交给检索。"""
    text = query.strip()
    for pattern, repl in COLLOQUIAL_RULES:
        text = re.sub(pattern, repl, text)
    return text


def expand_query(query: str) -> str:
    """归一 + 同义词扩展，供 BM25 召回使用。"""
    base = normalize_query(query)
    extras: list[str] = []
    for term, synonyms in _MAP.items():
        if term in base:
            extras.extend(s for s in synonyms if s not in base)
    if not extras:
        return base
    seen: set[str] = set()
    unique = [x for x in extras if not (x in seen or seen.add(x))]
    return f"{base} {' '.join(unique)}"
