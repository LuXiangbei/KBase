from __future__ import annotations

import re
from typing import Iterator

from .embed_hash import content_features, iter_features
from .rerank import coverage, split_sentences

REFUSAL_TEXT = (
    "这个问题在现有制度库里没有找到明确依据，我不做猜测。"
    "你可以补充具体条款名称或适用范围，我帮你再查；"
    "如果确实需要人工确认，我可以直接帮你创建一张工单。"
)

FILLER = "根据现有制度，"

# 被检索到的文档里可能藏着指令型文本（间接注入）。抽取式回答绝不能把它当作答案引用。
INJECTION_PATTERN = re.compile(
    r"忽略(以上|之前|上述)|系统提示词|你现在是|直接输出|不需要审批|年假为?\s*30\s*天|system\s*prompt",
    re.I,
)
NUMERIC_HINT = re.compile(r"多少|几天|几号|标准|上限|比例|时限|期限|金额|天数")


class LocalExtractiveLLM:
    """离线抽取式回答：从检索到的条款里挑最相关的句子，逐句带引用。

    它是"确定性基线"，用于无 Key 环境下的端到端验证；换成真实模型只改配置。
    """

    name = "local-extractive"

    def __init__(self, max_sentences: int = 3, min_sentence_score: float = 0.12):
        self.max_sentences = max_sentences
        self.min_sentence_score = min_sentence_score

    @staticmethod
    def _norm(sentence: str) -> str:
        return re.sub(r"[\s，。；：、,.!?！？:;\"'“”‘’]", "", sentence)

    @staticmethod
    def _similar(a: str, b: str) -> float:
        fa = set(iter_features(a))
        fb = set(iter_features(b))
        if not fa or not fb:
            return 0.0
        return len(fa & fb) / len(fa | fb)

    def _pick(self, question: str, contexts: list[dict]) -> list[tuple[float, int, str]]:
        from ..retrieval.query import expand_query, normalize_query

        q_orig = set(content_features(normalize_query(question)))
        q_all = set(content_features(expand_query(question)))
        wants_number = bool(NUMERIC_HINT.search(question))

        def scan(require_original: bool) -> list[tuple[float, int, str]]:
            picked: list[tuple[float, int, str]] = []
            seen: set[str] = set()
            for ctx in contexts:
                for sent in split_sentences(ctx["text"])[:24]:
                    if len(sent) < 10 or INJECTION_PATTERN.search(sent):
                        continue
                    key = self._norm(sent)
                    if key in seen:
                        continue
                    sent_feats = set(iter_features(sent))
                    hits_orig = q_orig & sent_feats
                    hits_all = q_all & sent_feats
                    if not hits_all:
                        continue
                    if require_original and not hits_orig:
                        continue
                    # 原查询命中权重更高，同义词只做补充，避免“延长工作时间”这类近义词把话题带跑
                    score = len(hits_orig) / (len(q_orig) or 1) + 0.25 * len(hits_all) / (len(q_all) or 1)
                    if wants_number and re.search(r"\d", sent):
                        score += 0.22
                    seen.add(key)
                    picked.append((score, ctx["n"], sent))
            picked.sort(key=lambda x: (-x[0], x[1]))
            return picked

        picked = scan(require_original=True) or scan(require_original=False)
        # 每个引用最多一句，且过滤近似重复（不同文档常常写同一句话）
        used_citations: set[int] = set()
        kept: list[str] = []
        result = []
        for score, n, sent in picked:
            if n in used_citations:
                continue
            if any(self._similar(sent, other) > 0.75 for other in kept):
                continue
            kept.append(sent)
            result.append((score, n, sent))
            used_citations.add(n)
            if len(result) >= self.max_sentences:
                break
        return result

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        if not contexts:
            return REFUSAL_TEXT
        picked = self._pick(question, contexts)
        if not picked:
            return REFUSAL_TEXT
        picked.sort(key=lambda x: x[1])
        lines = []
        for i, (_, n, sent) in enumerate(picked):
            prefix = FILLER if i == 0 else "此外，"
            lines.append(f"{prefix}{sent.rstrip('。')} [{n}]。")
        return "".join(lines)

    def stream(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> Iterator[str]:
        text = self.answer(question, contexts, history)
        step = 6
        for i in range(0, len(text), step):
            yield text[i : i + step]


class OpenAICompatLLM:
    """OpenAI 兼容流式接口（可选）。没有任何 Key 时不会被使用。"""

    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _messages(self, question: str, contexts: list[dict], history: list[dict] | None) -> list[dict]:
        blocks = [f"[{c['n']}]《{c['doc_title']}》{c['path']}（第 {c['page']} 页）\n{c['text']}" for c in contexts]
        system = (
            "你是企业制度问答助手。只依据给定资料回答，每个结论后用 [编号] 标注出处；"
            "资料不足时明确说不知道，不要编造条款号、金额或日期。"
        )
        user = "资料：\n" + "\n\n".join(blocks) + f"\n\n问题：{question}"
        msgs = [{"role": "system", "content": system}]
        for turn in (history or [])[-6:]:
            msgs.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        msgs.append({"role": "user", "content": user})
        return msgs

    def stream(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> Iterator[str]:
        import json

        import httpx

        payload = {"model": self.model, "messages": self._messages(question, contexts, history), "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=self.timeout) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        if not contexts:
            return REFUSAL_TEXT
        return "".join(self.stream(question, contexts, history))
