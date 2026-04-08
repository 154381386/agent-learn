from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..observability import get_observability
from ..runtime.contracts import (
    AgentDescriptor,
    AgentResult,
    ClarificationField,
    ClarificationRequest,
    FieldRequirement,
    TaskEnvelope,
    ValidationResult,
)


class BaseDomainAgent(ABC):
    name: str
    domain: str
    display_name: str = ""
    description: str = ""
    required_fields: list[FieldRequirement] = []
    capabilities: list[str] = []
    routing_keywords: list[str] = []

    async def run(self, task: TaskEnvelope) -> AgentResult:
        observability = get_observability()
        descriptor = self.descriptor()
        with observability.start_span(
            name=f"agent.{self.name}",
            as_type="span",
            input={
                "task_id": task.task_id,
                "ticket_id": task.ticket_id,
                "goal": task.goal,
                "mode": task.mode,
                "shared_context_keys": sorted((task.shared_context or {}).keys()),
            },
            metadata={
                "agent_name": self.name,
                "domain": self.domain,
                "display_name": descriptor.display_name,
                "required_fields": [field.name for field in descriptor.required_fields],
            },
        ) as span:
            validation = self.validate_context(self.context_for_validation(task))
            if not validation.valid:
                result = self.build_clarification_result(validation.missing_fields)
                span.update(
                    output={
                        "status": result.status,
                        "summary": result.summary,
                        "clarification_fields": [field.name for field in result.clarification_request.fields] if result.clarification_request else [],
                    }
                )
                return result
            try:
                result = await self.diagnose(task)
            except Exception as exc:
                span.update(level="ERROR", status_message=str(exc), metadata={"error_type": type(exc).__name__})
                raise
            span.update(
                output={
                    "status": result.status,
                    "summary": result.summary,
                    "risk_level": result.risk_level,
                    "tool_result_count": len(result.tool_results),
                }
            )
            return result

    @abstractmethod
    async def diagnose(self, task: TaskEnvelope) -> AgentResult:
        raise NotImplementedError

    def context_for_validation(self, task: TaskEnvelope) -> dict[str, Any]:
        return dict(task.shared_context or {})

    def descriptor(self) -> AgentDescriptor:
        override = getattr(self, "_descriptor_override", None)
        if isinstance(override, AgentDescriptor):
            return override.model_copy(deep=True)
        return AgentDescriptor(
            agent_name=self.name,
            domain=self.domain,
            display_name=self.display_name or self.name,
            description=self.description,
            required_fields=list(self.required_fields),
            capabilities=list(self.capabilities),
            routing_keywords=list(self.routing_keywords),
            tool_names=[],
        )

    def apply_descriptor(self, descriptor: AgentDescriptor) -> None:
        self._descriptor_override = descriptor.model_copy(deep=True)
        self.name = descriptor.agent_name
        self.domain = descriptor.domain
        self.display_name = descriptor.display_name
        self.description = descriptor.description
        self.required_fields = [field.model_copy(deep=True) for field in descriptor.required_fields]
        self.capabilities = list(descriptor.capabilities)
        self.routing_keywords = list(descriptor.routing_keywords)

    def validate_context(self, ctx: dict[str, Any]) -> ValidationResult:
        missing: list[FieldRequirement] = []
        for requirement in self.required_fields:
            value = ctx.get(requirement.name)
            if not self._is_field_satisfied(requirement, value):
                missing.append(requirement)
        return ValidationResult(valid=not missing, missing_fields=missing)

    def build_clarification_request(self, missing_fields: list[FieldRequirement]) -> ClarificationRequest:
        field_models = [
            ClarificationField(
                name=item.name,
                type=item.type,
                description=item.description,
                required=item.required,
                values=list(item.values),
                priority=item.priority,
                requested_by=[self.name],
            )
            for item in missing_fields
        ]
        if len(field_models) == 1:
            question = f"请补充{field_models[0].description}。"
        else:
            joined = "、".join(item.description for item in field_models)
            question = f"继续诊断前，请补充以下信息：{joined}。"
        return ClarificationRequest(
            agent_name=self.name,
            domain=self.domain,
            reason="缺少继续诊断所需的关键上下文字段。",
            question=question,
            fields=field_models,
        )

    def build_clarification_result(self, missing_fields: list[FieldRequirement]) -> AgentResult:
        request = self.build_clarification_request(missing_fields)
        return AgentResult(
            agent_name=self.name,
            domain=self.domain,
            status="awaiting_clarification",
            summary=request.reason,
            execution_path="clarification_required",
            findings=[],
            evidence=[],
            tool_results=[],
            recommended_actions=[],
            risk_level="low",
            confidence=0.0,
            open_questions=[request.question],
            needs_handoff=False,
            raw_refs=[],
            clarification_request=request,
        )

    async def run_tool_observed(self, tool: Any, task: TaskEnvelope, arguments: dict[str, Any] | None = None):
        observability = get_observability()
        context = self.context_for_validation(task)
        with observability.start_span(
            name=f"tool.{tool.name}",
            as_type="tool",
            input={
                "tool_name": tool.name,
                "arguments": arguments or {},
                "ticket_id": task.ticket_id,
                "service": context.get("service"),
            },
            metadata={
                "agent_name": self.name,
                "domain": self.domain,
            },
        ) as span:
            try:
                result = await tool.run(task, arguments=arguments)
            except Exception as exc:
                span.update(level="ERROR", status_message=str(exc), metadata={"error_type": type(exc).__name__})
                raise
            span.update(
                output={
                    "status": result.status,
                    "summary": result.summary,
                    "risk": result.risk,
                }
            )
            return result

    @staticmethod
    def _is_field_satisfied(requirement: FieldRequirement, value: Any) -> bool:
        if value in (None, ""):
            return False
        if requirement.type == "enum" and requirement.values:
            return str(value) in {str(item) for item in requirement.values}
        return True
