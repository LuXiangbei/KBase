from __future__ import annotations

import pytest

from app.agent.orchestrator import guard_output
from app.agent.router import is_complex, route_mode
from app.providers.llm_deepseek import DeepSeekLLM, LLMError, ResilientLLM, build_messages
from app.providers.llm_local import LocalExtractiveLLM

CONTEXTS = [
    {"n": 1, "doc_title": "休假与请假管理办法", "path": "第1条 年假", "page": 1,
     "text": "累计工龄满 1 年不满 10 年的，年休假 5 天；满 10 年不满 20 年的，年休假 10 天。"},
]


@pytest.mark.parametrize(
    "question,complex_",
    [
        ("年假有几天？", False),
        ("住宿标准是多少？", False),
        ("我已休3天年假，工龄10年，还剩几天？", True),
        ("工龄八年，年假几天", True),
        ("年假和调休分别怎么算？", True),
    ],
)
def test_is_complex(question, complex_):
    assert is_complex(question) is complex_


def test_route_mode_uses_history_for_short_followups():
    history = [{"role": "user", "content": "我有多少年假"}, {"role": "assistant", "content": "请告知工龄"}]
    assert route_mode("八年", history) == "simple"  # 短回答本身不复杂，但检索会拼上文
    assert route_mode("我工龄八年，年假几天", history) == "complex"


def test_build_messages_contains_rules_and_context():
    messages = build_messages("年假几天？", CONTEXTS, [{"role": "user", "content": "你好"}], "simple")
    assert messages[0]["role"] == "system"
    assert "资料" in messages[0]["content"] and "不要编造" in messages[0]["content"]
    assert "<资料>" in messages[-1]["content"] and "<问题>" in messages[-1]["content"]
    assert "[1]" in messages[-1]["content"]
    assert any(m["content"] == "你好" for m in messages)


def test_complex_prompt_asks_for_calculation_steps():
    messages = build_messages("已休3天，工龄10年，还剩几天？", CONTEXTS, None, "complex")
    assert "计算" in messages[0]["content"]
    simple = build_messages("年假几天？", CONTEXTS, None, "simple")
    assert "3 句以内" in simple[0]["content"]


def test_payload_selects_model_and_budget():
    llm = DeepSeekLLM("https://api.deepseek.com", "sk-test", "deepseek-flash", "deepseek-v4-pro")
    simple = llm.payload("年假几天？", CONTEXTS, None, "simple")
    complex_ = llm.payload("已休3天，工龄10年", CONTEXTS, None, "complex")
    assert simple["model"] == "deepseek-flash"
    assert complex_["model"] == "deepseek-flash"
    assert simple["max_tokens"] < complex_["max_tokens"]
    assert simple["stream"] is True and simple["stream_options"]["include_usage"] is True


def test_payload_deep_mode_uses_deep_model():
    llm = DeepSeekLLM("https://api.deepseek.com", "sk-test", "deepseek-flash", "deepseek-v4-pro")
    assert llm.payload("难问题", CONTEXTS, None, "deep")["model"] == "deepseek-v4-pro"


def test_resilient_falls_back_when_primary_fails():
    class Broken:
        def stream(self, *args, **kwargs):
            raise LLMError("boom")
            yield ""  # pragma: no cover

    llm = ResilientLLM(Broken(), LocalExtractiveLLM())
    text = "".join(llm.stream("年假几天？", CONTEXTS))
    assert "5 天" in text
    assert llm.last_engine == "fallback"


def test_guard_output_blocks_key_and_prompt_leak():
    text, blocked = guard_output("这是系统提示词，另外 key 是 sk-abcdefghijklmn")
    assert blocked is True
    assert "sk-" not in text
    safe, blocked2 = guard_output("年休假 5 天 [1]。")
    assert blocked2 is False and safe == "年休假 5 天 [1]。"
