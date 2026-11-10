from __future__ import annotations

import threading
import time
from collections import deque


class Metrics:
    def __init__(self, window: int = 500):
        self._lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cache_hits = 0
        self.cost = 0.0
        self.latencies: deque[float] = deque(maxlen=window)
        self.first_token: deque[float] = deque(maxlen=window)

    def record_turn(self, latency_ms: float, tokens_in: int, tokens_out: int, cost: float = 0.0, ttft_ms: float | None = None) -> None:
        with self._lock:
            self.requests += 1
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.cost += cost
            self.latencies.append(latency_ms)
            if ttft_ms is not None:
                self.first_token.append(ttft_ms)

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    @staticmethod
    def _p95(values: deque[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
        return round(ordered[idx], 2)

    def snapshot(self) -> dict:
        with self._lock:
            avg = round(sum(self.latencies) / len(self.latencies), 2) if self.latencies else 0.0
            return {
                "requests": self.requests,
                "errors": self.errors,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "cost_cny": round(self.cost, 6),
                "latency_avg_ms": avg,
                "latency_p95_ms": self._p95(self.latencies),
                "ttft_p95_ms": self._p95(self.first_token),
                "cache_hits": self.cache_hits,
                "ts": time.time(),
            }


METRICS = Metrics()
