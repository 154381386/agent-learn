from __future__ import annotations

from typing import Any

from .models import ExecutionRetryPolicy


def default_retry_policy(action: str, *, risk: str = "low", step_kind: str = "tool") -> ExecutionRetryPolicy:
    if step_kind in {"precheck", "postcheck"}:
        return ExecutionRetryPolicy(
            max_attempts=1,
            backoff_seconds=0,
            strategy="fixed",
            retryable_errors=[],
            operator_hint="内部执行控制步骤失败时不应自动重试，应先检查上下文和快照一致性。",
        )

    normalized_risk = str(risk or "low").lower()
    if normalized_risk in {"high", "critical"}:
        return ExecutionRetryPolicy(
            max_attempts=2,
            backoff_seconds=15,
            strategy="exponential",
            retryable_errors=["TimeoutError", "ConnectionError", "RuntimeError"],
            operator_hint="高风险动作最多自动重试一次；若仍失败，应先人工确认资源状态。",
        )
    return ExecutionRetryPolicy(
        max_attempts=2,
        backoff_seconds=5,
        strategy="fixed",
        retryable_errors=["TimeoutError", "ConnectionError"],
        operator_hint="低风险动作可短暂重试；若错误持续存在，应转入人工排查。",
    )


def retry_state_for_attempt(policy: ExecutionRetryPolicy, attempt: int, error: Exception | None = None) -> dict[str, Any]:
    error_name = type(error).__name__ if error is not None else ""
    if error is None:
        retryable = attempt < policy.max_attempts
    elif not policy.retryable_errors:
        retryable = attempt < policy.max_attempts
    else:
        retryable = error_name in set(policy.retryable_errors) and attempt < policy.max_attempts
    return {
        "attempt": attempt,
        "max_attempts": policy.max_attempts,
        "retryable": retryable,
        "remaining_attempts": max(policy.max_attempts - attempt, 0),
        "error_type": error_name,
        "operator_hint": policy.operator_hint,
    }
