from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from threading import Lock

MAX_TRACES = 200


@dataclass
class Trace:
    trace_id: str
    ts: float
    user_id: str
    intent: str
    state: str
    question: str
    retrieval: dict = field(default_factory=dict)
    citations: int = 0
    answer_chars: int = 0
    usage: dict = field(default_factory=dict)
    latency_ms: dict = field(default_factory=dict)
    cost_cny: float = 0.0
    cached: bool = False
    mode: str = ""
    model: str = ""
    engine: str = ""
    ticket_id: str | None = None


_TRACES: "OrderedDict[str, Trace]" = OrderedDict()
_LOCK = Lock()


def record(trace: Trace) -> Trace:
    with _LOCK:
        _TRACES[trace.trace_id] = trace
        while len(_TRACES) > MAX_TRACES:
            _TRACES.popitem(last=False)
    return trace


def get(trace_id: str) -> dict | None:
    with _LOCK:
        trace = _TRACES.get(trace_id)
        return asdict(trace) if trace else None


def recent(limit: int = 20) -> list[dict]:
    with _LOCK:
        items = list(_TRACES.values())[-limit:]
    return [asdict(t) for t in reversed(items)]


def clear() -> None:
    with _LOCK:
        _TRACES.clear()
