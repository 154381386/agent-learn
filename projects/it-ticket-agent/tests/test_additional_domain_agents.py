from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from it_ticket_agent.agents.db import DBAgent
from it_ticket_agent.agents.finops import FinOpsAgent
from it_ticket_agent.agents.network import NetworkAgent
from it_ticket_agent.agents.sde import SDEAgent
from it_ticket_agent.runtime.contracts import TaskEnvelope
from it_ticket_agent.settings import Settings
from it_ticket_agent.tools.db import InspectDBInstanceHealthTool
from it_ticket_agent.tools.finops import InspectCostAnomalyTool
from it_ticket_agent.tools.network import InspectDNSResolutionTool
from it_ticket_agent.tools.sde import InvestigateResourceProvisioningTool


class AdditionalDomainAgentsTest(unittest.IsolatedAsyncioTestCase):
    def build_task(self, *, message: str, service: str, scenario: str = "error") -> TaskEnvelope:
        return TaskEnvelope(
            task_id="task-extra",
            ticket_id="ticket-extra",
            goal="诊断并给出下一步建议",
            shared_context={
                "message": message,
                "service": service,
                "cluster": "prod-shanghai-1",
                "namespace": "default",
                "mock_scenario": scenario,
            },
        )

    async def test_domain_tools_use_json_profiles(self) -> None:
        with patch.dict(os.environ, {"IT_TICKET_AGENT_MOCK_SCENARIO": "error"}, clear=False):
            sde = await InvestigateResourceProvisioningTool().run(self.build_task(message="资源开通失败", service="车云服务"))
            network = await InspectDNSResolutionTool().run(self.build_task(message="dns 解析异常", service="支付服务"))
            finops = await InspectCostAnomalyTool().run(self.build_task(message="成本突增", service="车云服务"))
            db = await InspectDBInstanceHealthTool().run(self.build_task(message="数据库实例异常", service="支付服务"))

        self.assertEqual(sde.payload["request_id"], "REQ-SDE-1001")
        self.assertEqual(network.payload["resolution_status"], "degraded")
        self.assertEqual(finops.payload["anomaly_status"], "suspected")
        self.assertEqual(db.payload["db_health"], "degraded")

    async def test_agents_can_run_fallback_without_registration(self) -> None:
        settings = Settings(llm_base_url="", llm_api_key="", llm_model="")
        with patch.dict(os.environ, {"IT_TICKET_AGENT_MOCK_SCENARIO": "error"}, clear=False):
            sde_result = await SDEAgent(settings).run(self.build_task(message="资源开通失败", service="车云服务"))
            network_result = await NetworkAgent(settings).run(self.build_task(message="网络异常", service="支付服务"))
            finops_result = await FinOpsAgent(settings).run(self.build_task(message="成本异常", service="车云服务"))
            db_result = await DBAgent(settings).run(self.build_task(message="数据库异常", service="支付服务"))

        self.assertEqual(sde_result.agent_name, "sde_agent")
        self.assertEqual(network_result.agent_name, "network_agent")
        self.assertEqual(finops_result.agent_name, "finops_agent")
        self.assertEqual(db_result.agent_name, "db_agent")
        self.assertGreaterEqual(len(sde_result.tool_results), 1)
        self.assertGreaterEqual(len(network_result.tool_results), 1)
        self.assertGreaterEqual(len(finops_result.tool_results), 1)
        self.assertGreaterEqual(len(db_result.tool_results), 1)


if __name__ == "__main__":
    unittest.main()
