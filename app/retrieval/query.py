from __future__ import annotations

# 领域同义词：用户说“年假”，制度写“年休假”。离线基线没有语义模型，靠术语归一补这一层。
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("年假", "年休假"),
    ("调休", "补休"),
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
    ("加班", "延长工作时间", "补休"),
    ("出差", "差旅"),
    ("培训", "学习"),
    ("盘点", "清点"),
    ("招待", "宴请"),
    ("保密", "保密义务"),
    ("模板", "范本"),
    ("预算", "费用预算"),
)

_MAP: dict[str, tuple[str, ...]] = {}
for group in SYNONYM_GROUPS:
    for term in group:
        _MAP[term] = tuple(t for t in group if t != term)


def expand_query(query: str) -> str:
    """返回“原查询 + 命中的同义词”，供 BM25 / 向量 / 覆盖率共用。"""
    extras: list[str] = []
    for term, synonyms in _MAP.items():
        if term in query:
            extras.extend(s for s in synonyms if s not in query)
    if not extras:
        return query
    seen: set[str] = set()
    unique = [x for x in extras if not (x in seen or seen.add(x))]
    return f"{query} {' '.join(unique)}"
