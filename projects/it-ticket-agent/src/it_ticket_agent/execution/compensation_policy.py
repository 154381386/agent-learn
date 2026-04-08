from __future__ import annotations

from .models import ExecutionCompensationPolicy


def default_compensation_policy(action: str, *, risk: str = "low") -> ExecutionCompensationPolicy | None:
    normalized_action = str(action or "")
    normalized_risk = str(risk or "low").lower()
    if normalized_action.startswith("cicd.rollback"):
        return ExecutionCompensationPolicy(
            mode="manual",
            action="manual.verify_release_state",
            params={"scope": "service_release_state"},
            reason="回滚类动作失败后，不能盲目自动补偿，需要人工确认版本、流量和 Pod 状态。",
            operator_hint="确认当前服务版本、变更窗口和流量切换状态，再决定是否再次回滚或正向修复。",
        )
    if normalized_risk in {"high", "critical"}:
        return ExecutionCompensationPolicy(
            mode="manual",
            action="manual.assess_service_state",
            params={"scope": "high_risk_action"},
            reason="高风险动作失败后默认需要人工评估是否执行补偿。",
            operator_hint="先确认故障面和目标资源状态，再决定后续补偿路径。",
        )
    return None
