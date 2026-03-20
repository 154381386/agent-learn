from __future__ import annotations

import logging
from typing import Dict

from ..agents import CICDAgent, GeneralSREAgent
from ..approval_store import ApprovalStore
from ..mcp import MCPConnectionManager
from ..mcp import MCPClient
from ..schemas import ApprovalDecisionRequest
from ..schemas import TicketRequest
from ..settings import Settings
from .contracts import AgentAction, AgentResult
from .supervisor import RuleBasedSupervisor


logger = logging.getLogger(__name__)


class SupervisorOrchestrator:
    def __init__(self, settings: Settings, approval_store: ApprovalStore) -> None:
        self.settings = settings
        self.approval_store = approval_store
        self.supervisor = RuleBasedSupervisor()
        self.connection_manager = MCPConnectionManager(settings.mcp_connections_path)
        self.agents: Dict[str, object] = {
            "cicd_agent": CICDAgent(
                self.supervisor.knowledge_client(settings),
                self.connection_manager,
            ),
            "general_sre_agent": GeneralSREAgent(),
        }

    async def handle_ticket(self, request: TicketRequest) -> Dict[str, object]:
        decision = self.supervisor.route(request)
        task = self.supervisor.build_task(request, decision)
        agent = self.agents[decision.agent_name]
        result = await agent.run(task)

        logger.info(
            "supervisor_router_resolved ticket_id=%s agent=%s mode=%s confidence=%.2f",
            request.ticket_id,
            decision.agent_name,
            decision.mode,
            decision.confidence,
        )
        logger.info(
            "subagent_completed ticket_id=%s agent=%s domain=%s",
            request.ticket_id,
            result.agent_name,
            result.domain,
        )

        approval_request = self.maybe_create_approval(request, result)
        if approval_request is not None:
            return {
                "ticket_id": request.ticket_id,
                "status": "awaiting_approval",
                "message": "检测到高风险动作，需审批后才能继续执行。",
                "approval_request": approval_request,
                "diagnosis": self.render_diagnosis(result, decision),
            }

        return {
            "ticket_id": request.ticket_id,
            "status": "completed",
            "message": self.render_response(result),
            "diagnosis": self.render_diagnosis(result, decision),
        }

    async def handle_approval_decision(
        self,
        approval: Dict[str, object],
        request: ApprovalDecisionRequest,
    ) -> Dict[str, object]:
        params = approval.get("params", {})
        action = approval.get("action", "")
        if not request.approved:
            message = "审批未通过，未执行任何高风险动作。"
            return {
                "ticket_id": approval["ticket_id"],
                "status": "completed",
                "message": message,
                "diagnosis": {
                    "approval": {
                        "approval_id": approval["approval_id"],
                        "action": action,
                        "status": "rejected",
                    }
                },
            }

        tool_params = {
            key: value
            for key, value in params.items()
            if key not in {"orchestration_mode", "mcp_server", "agent_name"}
        }
        mcp_server = params.get("mcp_server")
        if not mcp_server:
            raise ValueError("approval params missing mcp_server")

        client = MCPClient(str(mcp_server))
        execution = await client.call_tool(str(action), tool_params)
        execution_payload = execution.get("structuredContent", {})
        summary = execution.get("content", [{}])[0].get("text", "高风险动作已执行。")
        if execution_payload.get("status") == "pending_approval":
            summary = "已向执行系统提交高风险动作，请继续跟踪执行状态。"
        return {
            "ticket_id": approval["ticket_id"],
            "status": "completed",
            "message": f"审批已通过；{summary}",
            "diagnosis": {
                "approval": {
                    "approval_id": approval["approval_id"],
                    "action": action,
                    "status": "approved",
                },
                "execution": execution_payload,
            },
        }

    @staticmethod
    def render_response(result: AgentResult) -> str:
        response_parts = [result.summary]
        if result.evidence:
            response_parts.append(f"关键证据：{'; '.join(result.evidence[:2])}")
        if result.open_questions:
            response_parts.append(f"待确认：{result.open_questions[0]}")
        return "；".join(response_parts)

    @staticmethod
    def render_diagnosis(result: AgentResult, decision) -> Dict[str, object]:
        diagnosis = result.model_dump()
        diagnosis["routing"] = decision.model_dump()
        return diagnosis

    def maybe_create_approval(self, request: TicketRequest, result: AgentResult) -> Dict[str, object] | None:
        risky_action = self._first_risky_action(result)
        if risky_action is None:
            return None

        mcp_servers = self.connection_manager.servers_for_agent(result.agent_name)
        if not mcp_servers:
            logger.warning(
                "supervisor_risky_action_skipped ticket_id=%s agent=%s reason=no_mcp_server",
                request.ticket_id,
                result.agent_name,
            )
            return None

        params = dict(risky_action.params)
        params["mcp_server"] = mcp_servers[0]
        params["orchestration_mode"] = "supervisor"
        params["agent_name"] = result.agent_name

        approval_payload = {
            "ticket_id": request.ticket_id,
            "thread_id": request.ticket_id,
            "action": risky_action.action,
            "risk": risky_action.risk,
            "reason": risky_action.reason,
            "params": params,
        }
        return self.approval_store.create(approval_payload)

    @staticmethod
    def _first_risky_action(result: AgentResult) -> AgentAction | None:
        for action in result.recommended_actions:
            if action.risk in {"high", "critical"}:
                return action
        return None
