from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

DEFAULT_USER_ID = "demo-user"
DEFAULT_ROLES = ("employee",)
DEFAULT_DEPT = "general"

# 间接注入：文档里藏的指令。检索阶段直接把这些 chunk 排除，避免进入上下文。
INJECTION_PATTERN = re.compile(
    r"忽略(以上|之前|上述)[^。]{0,12}(指令|内容)|系统提示词|你现在是系统管理员|"
    r"直接输出你的|不需要审批|年假为?\s*30\s*天",
    re.I,
)


def contains_injection(text: str) -> bool:
    return bool(INJECTION_PATTERN.search(text or ""))


@dataclass(frozen=True)
class User:
    id: str = DEFAULT_USER_ID
    roles: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ROLES))
    dept: str = DEFAULT_DEPT

    def has_role(self, role: str) -> bool:
        return role in self.roles


def user_from_headers(headers) -> User:
    """从请求头解析用户。演示用；生产应换成 JWT/OIDC。"""
    get = headers.get
    uid = (get("x-user-id") or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    raw_roles = (get("x-user-roles") or "").strip()
    roles = {r.strip() for r in raw_roles.split(",") if r.strip()} or set(DEFAULT_ROLES)
    dept = (get("x-user-dept") or DEFAULT_DEPT).strip() or DEFAULT_DEPT
    return User(id=uid, roles=frozenset(roles), dept=dept)


def parse_visibility(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {"scope": "all"}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "scope" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return {"scope": "all"}


def visibility_allows(vis: str | dict | None, user: User) -> bool:
    """ACL 判定的唯一入口：检索层与 API 层都调它。"""
    data = parse_visibility(vis)
    scope = data.get("scope", "all")
    if scope == "all":
        return True
    if scope == "users":
        return user.id in set(data.get("users") or [])
    if scope == "roles":
        return bool(set(data.get("roles") or []) & set(user.roles))
    if scope == "depts":
        return user.dept in set(data.get("depts") or [])
    return False


def is_effective(row, today: date | None = None) -> bool:
    """文档时效：effective_to 为空或未过期。"""
    today = today or date.today()
    end = row["effective_to"] if "effective_to" in row.keys() else None
    if not end:
        return True
    try:
        return date.fromisoformat(str(end)[:10]) >= today
    except ValueError:
        return True


def can_read_document(row, user: User, today: date | None = None) -> bool:
    if row["deleted_at"]:
        return False
    if row["status"] != "ready":
        return False
    if not is_effective(row, today):
        return False
    return visibility_allows(row["visibility"], user)
