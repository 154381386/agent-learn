from __future__ import annotations

import json
from typing import Any

from ..llm_client import OpenAICompatToolLLM
from ..settings import Settings
from ..state.models import ContextSnapshot, Hypothesis, VerificationStep


class HypothesisGenerator:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: OpenAICompatToolLLM | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm or OpenAICompatToolLLM(settings)

    async def generate(self, context_snapshot: ContextSnapshot) -> list[Hypothesis]:
        if self.llm.enabled:
            hypotheses = await self._generate_with_llm(context_snapshot)
            if hypotheses:
                return hypotheses
        return self._generate_with_rules(context_snapshot)

    async def _generate_with_llm(self, context_snapshot: ContextSnapshot) -> list[Hypothesis]:
        request = dict(context_snapshot.request or {})
        rag_context = context_snapshot.rag_context or {}
        similar_cases = [case.model_dump() for case in context_snapshot.similar_cases[:3]]
        available_skills = [skill.model_dump() for skill in context_snapshot.available_skills]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 IT 运维根因分析专家。"
                    "请基于上下文生成 1-3 个结构化根因假设，并输出 JSON。"
                    "输出格式必须是 {\"hypotheses\": [...]}。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": request,
                        "rag_context": rag_context.model_dump() if hasattr(rag_context, "model_dump") else rag_context,
                        "similar_cases": similar_cases,
                        "available_skills": available_skills,
                        "rules": {
                            "max_hypotheses": 3,
                            "min_hypotheses": 1,
                            "use_only_available_skills": True,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = await self.llm.chat(messages)
            payload = self.llm.extract_json(str(response.get("content") or ""))
            items = payload.get("hypotheses") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return []
            return [Hypothesis.model_validate(item) for item in items][:3]
        except Exception:
            return []

    def _generate_with_rules(self, context_snapshot: ContextSnapshot) -> list[Hypothesis]:
        request = dict(context_snapshot.request or {})
        service = str(request.get("service") or "service")
        message = str(request.get("message") or "").lower()
        categories = list(context_snapshot.matched_skill_categories or [])
        if not categories and list(context_snapshot.available_skills or []):
            categories = sorted({item.category for item in context_snapshot.available_skills})
        available_skill_names = {item.name for item in context_snapshot.available_skills}
        crash_keywords = ("crash", "crashloop", "oom", "oomkilled", "崩溃", "重启")
        low_risk_keywords = ("低风险", "自动修复", "自动恢复", "低风险自动", "observe", "观测")

        hypotheses: list[Hypothesis] = []

        if any(keyword in message for keyword in low_risk_keywords):
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H1",
                    root_cause="低风险自动化动作即可恢复服务",
                    confidence_prior=0.88,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_log_errors",
                            params={"service": service, "window": "30m"},
                            purpose="先确认是否存在可通过低风险观测或缓解动作处理的异常模式",
                        )
                    ],
                    expected_evidence="存在明确异常，但无需立即执行高风险动作。",
                    recommended_action="observe_service",
                    action_risk="low",
                    action_params={"service": service},
                )
            )

        if "cicd" in categories:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"H{len(hypotheses) + 1}",
                    root_cause="最近发布或流水线变更导致服务回归",
                    confidence_prior=0.78 if context_snapshot.similar_cases else 0.72,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_recent_deploys",
                            params={"service": service},
                            purpose="确认故障时间窗内是否存在部署或配置变更",
                        ),
                        VerificationStep(
                            skill_name="check_pipeline_status",
                            params={"project": service, "branch": "main"},
                            purpose="确认最近一次流水线是否失败或异常发布",
                        ),
                    ],
                    expected_evidence="最近部署时间与故障时间窗重合，或流水线状态异常。",
                    recommended_action="rollback_deploy",
                    action_risk="high",
                    action_params={"service": service},
                )
            )

        if "network" in categories:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"H{len(hypotheses) + 1}",
                    root_cause="入口流量、Ingress 或上游网络链路异常导致 5xx / timeout",
                    confidence_prior=0.74,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_ingress_rules",
                            params={"service": service},
                            purpose="检查入口路由、超时与重试配置是否异常",
                        ),
                        VerificationStep(
                            skill_name="check_network_latency",
                            params={"service": service, "upstream": "unknown-upstream"},
                            purpose="确认是否存在链路延迟抖动或上游调用异常",
                        ),
                    ],
                    expected_evidence="Ingress 配置异常、或链路延迟与错误率同时升高。",
                    recommended_action="",
                    action_risk="low",
                    action_params={},
                )
            )

        if "k8s" in categories:
            use_pack_skill = "diagnose_pod_crash" in available_skill_names and any(keyword in message for keyword in crash_keywords)
            verification_plan = (
                [
                    VerificationStep(
                        skill_name="diagnose_pod_crash",
                        params={"service": service, "namespace": str(request.get("namespace") or "default")},
                        purpose="按 Pod 崩溃 SOP 一次性检查状态、事件、日志与资源信号",
                    )
                ]
                if use_pack_skill
                else [
                    VerificationStep(
                        skill_name="check_pod_health",
                        params={"service": service},
                        purpose="确认 Pod 是否存在重启、CrashLoop 或健康探针失败",
                    ),
                    VerificationStep(
                        skill_name="check_memory_trend",
                        params={"service": service, "window": "30m"},
                        purpose="确认是否存在内存上涨、OOM 或资源打满",
                    ),
                ]
            )
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"H{len(hypotheses) + 1}",
                    root_cause="Pod 健康异常或资源不足导致服务不稳定",
                    confidence_prior=0.74 if use_pack_skill else 0.7,
                    verification_plan=verification_plan,
                    expected_evidence="Pod 异常重启、探针失败或内存趋势持续恶化。",
                    recommended_action="restart_pods",
                    action_risk="high",
                    action_params={"service": service},
                )
            )

        if "db" in categories and len(hypotheses) < 3:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"H{len(hypotheses) + 1}",
                    root_cause="数据库连接池或慢查询异常拖慢请求处理",
                    confidence_prior=0.66,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_db_health",
                            params={"service": service},
                            purpose="确认连接池、慢查询和数据库健康状态",
                        )
                    ],
                    expected_evidence="连接池耗尽、慢查询升高或数据库实例异常。",
                    recommended_action="",
                    action_risk="low",
                    action_params={},
                )
            )

        if "monitor" in categories and len(hypotheses) < 3:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"H{len(hypotheses) + 1}",
                    root_cause="日志与告警显示应用运行时异常正在放大",
                    confidence_prior=0.62,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_log_errors",
                            params={"service": service, "window": "30m"},
                            purpose="聚合最近错误日志，确认主要异常类型",
                        ),
                        VerificationStep(
                            skill_name="check_alert_history",
                            params={"service": service},
                            purpose="核对告警在故障窗口内的触发模式",
                        ),
                    ],
                    expected_evidence="错误日志聚集、告警频率上升且与故障时间窗一致。",
                    recommended_action="",
                    action_risk="low",
                    action_params={},
                )
            )

        if not hypotheses:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id="H1",
                    root_cause="应用运行时异常导致服务不稳定，需要先从日志和变更入手确认",
                    confidence_prior=0.55,
                    verification_plan=[
                        VerificationStep(
                            skill_name="check_log_errors",
                            params={"service": service, "window": "30m"},
                            purpose="先确认最近错误日志与主要异常模式",
                        )
                    ],
                    expected_evidence="日志中存在清晰错误模式，能够缩小根因范围。",
                    recommended_action="",
                    action_risk="low",
                    action_params={},
                )
            )

        return hypotheses[:3]
