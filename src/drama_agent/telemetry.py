"""Per-workflow telemetry for latency, LLM usage, failures and retrieval."""
from __future__ import annotations

import contextvars
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RunTelemetry:
    started_at: float = field(default_factory=time.time)
    node_durations_ms: Dict[str, float] = field(default_factory=dict)
    node_errors: List[str] = field(default_factory=list)
    llm_calls: int = 0
    llm_failed_calls: int = 0
    stub_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_usage_calls: int = 0
    retrieved_count: int = 0
    strict_llm: bool = False
    max_total_tokens: Optional[int] = None
    token_budget_exhausted: bool = False

    def snapshot(self, elapsed_ms: Optional[float] = None) -> Dict[str, Any]:
        elapsed = elapsed_ms if elapsed_ms is not None else (time.time() - self.started_at) * 1000
        successful_calls = max(0, self.llm_calls - self.llm_failed_calls)
        return {
            "elapsed_ms": round(float(elapsed), 1),
            "node_durations_ms": {
                name: round(value, 1) for name, value in self.node_durations_ms.items()
            },
            "node_errors": list(self.node_errors),
            "llm_calls": self.llm_calls,
            "llm_successful_calls": successful_calls,
            "llm_failed_calls": self.llm_failed_calls,
            "stub_calls": self.stub_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_estimated": self.estimated_usage_calls > 0,
            "retrieved_count": self.retrieved_count,
            "token_budget_limit": self.max_total_tokens,
            "token_budget_remaining": (
                max(0, self.max_total_tokens - self.total_tokens)
                if self.max_total_tokens is not None else None
            ),
            "token_budget_exhausted": self.token_budget_exhausted,
        }


_current: contextvars.ContextVar[Optional[RunTelemetry]] = contextvars.ContextVar(
    "drama_run_telemetry", default=None
)


def start_run_telemetry(
    strict_llm: bool = False,
    max_total_tokens: Optional[int] = None,
) -> RunTelemetry:
    telemetry = RunTelemetry(
        strict_llm=strict_llm,
        max_total_tokens=max_total_tokens,
    )
    _current.set(telemetry)
    return telemetry


def current_telemetry() -> Optional[RunTelemetry]:
    return _current.get()


def strict_llm_required() -> bool:
    telemetry = current_telemetry()
    return bool(telemetry and telemetry.strict_llm)


def ensure_llm_token_budget(prompt_chars: int, max_completion_tokens: int) -> None:
    """在请求前按最坏输出量预留额度，防止一次样本内部继续超支。"""
    telemetry = current_telemetry()
    if telemetry is None or telemetry.max_total_tokens is None:
        return
    estimated_prompt = math.ceil(max(0, prompt_chars) / 4)
    projected = telemetry.total_tokens + estimated_prompt + max(0, max_completion_tokens)
    if projected <= telemetry.max_total_tokens:
        return
    telemetry.token_budget_exhausted = True
    from .exceptions import TokenBudgetExceededError

    raise TokenBudgetExceededError(
        f"Token 预算不足：剩余 {max(0, telemetry.max_total_tokens - telemetry.total_tokens)}，"
        f"本次请求最多需要 {estimated_prompt + max(0, max_completion_tokens)}"
    )


def record_node(name: str, duration_ms: float, error: Optional[str] = None) -> None:
    telemetry = current_telemetry()
    if telemetry is None:
        return
    telemetry.node_durations_ms[name] = telemetry.node_durations_ms.get(name, 0.0) + duration_ms
    if error:
        telemetry.node_errors.append(f"{name}: {error}")


def record_retrieval(count: int) -> None:
    telemetry = current_telemetry()
    if telemetry is not None:
        telemetry.retrieved_count += max(0, int(count))


def record_llm_call(
    *,
    prompt_chars: int,
    completion_chars: int = 0,
    usage: Optional[Dict[str, Any]] = None,
    success: bool,
) -> None:
    telemetry = current_telemetry()
    if telemetry is None:
        return
    telemetry.llm_calls += 1
    if not success:
        telemetry.llm_failed_calls += 1
    usage = usage or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    if prompt_tokens is None or completion_tokens is None:
        prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        completion_tokens = max(1, completion_chars // 4) if completion_chars else 0
        total_tokens = prompt_tokens + completion_tokens
        telemetry.estimated_usage_calls += 1
    telemetry.prompt_tokens += int(prompt_tokens or 0)
    telemetry.completion_tokens += int(completion_tokens or 0)
    telemetry.total_tokens += int(total_tokens or (prompt_tokens or 0) + (completion_tokens or 0))


def record_stub_call(prompt_chars: int, completion_chars: int) -> None:
    telemetry = current_telemetry()
    if telemetry is None:
        return
    telemetry.stub_calls += 1
    # Stub 不产生计费 Token，字符数不计入 LLM token 指标。
