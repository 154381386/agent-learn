from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from it_ticket_agent.agent_registry import AgentRegistryLoader
from it_ticket_agent.agents import AgentFactory
from it_ticket_agent.mcp import MCPConnectionManager
from it_ticket_agent.rag_client import RAGServiceClient
from it_ticket_agent.settings import Settings


class AgentRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(rag_enabled=False)

    def test_loader_reads_default_registry_and_respects_enabled_flags(self) -> None:
        registry = AgentRegistryLoader(self.settings.agent_registry_path).load()

        self.assertEqual(
            [entry.descriptor.agent_name for entry in registry.enabled_entries()],
            ["cicd_agent", "general_sre_agent", "network_agent"],
        )
        self.assertEqual(
            [entry.descriptor.agent_name for entry in registry.routable_entries()],
            ["cicd_agent", "network_agent", "general_sre_agent"],
        )

    def test_factory_builds_only_enabled_agents_and_applies_registry_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry_dir = Path(tmp_dir)
            registry_dir.joinpath("custom_agent.yaml").write_text(
                textwrap.dedent(
                    """
                    version: 1
                    enabled: true
                    implementation: it_ticket_agent.agents.general.GeneralSREAgent
                    routing:
                      enabled: true
                      priority: 5
                    descriptor:
                      agent_name: custom_general_agent
                      domain: general
                      display_name: Custom General Agent
                      description: 用于验证 registry descriptor 会成为 canonical model。
                      required_fields: []
                      capabilities:
                        - custom_triage
                      routing_keywords:
                        - 自定义
                      tool_names: []
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            registry_dir.joinpath("disabled_agent.yaml").write_text(
                textwrap.dedent(
                    """
                    version: 1
                    enabled: false
                    implementation: it_ticket_agent.agents.general.GeneralSREAgent
                    routing:
                      enabled: false
                      priority: 10
                    descriptor:
                      agent_name: disabled_general_agent
                      domain: general
                      display_name: Disabled General Agent
                      description: disabled
                      required_fields: []
                      capabilities: []
                      routing_keywords: []
                      tool_names: []
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            registry = AgentRegistryLoader(registry_dir).load()
            factory = AgentFactory(
                settings=self.settings,
                connection_manager=MCPConnectionManager(self.settings.mcp_connections_path),
                knowledge_client=RAGServiceClient(self.settings),
            )

            agents = factory.build_agents(registry)

        self.assertEqual(list(agents.keys()), ["custom_general_agent"])
        descriptor = agents["custom_general_agent"].descriptor()
        self.assertEqual(descriptor.agent_name, "custom_general_agent")
        self.assertEqual(descriptor.display_name, "Custom General Agent")
        self.assertEqual(descriptor.capabilities, ["custom_triage"])
        self.assertEqual(descriptor.routing_keywords, ["自定义"])

    def test_loader_rejects_duplicate_agent_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry_dir = Path(tmp_dir)
            payload = textwrap.dedent(
                """
                version: 1
                enabled: true
                implementation: it_ticket_agent.agents.general.GeneralSREAgent
                routing:
                  enabled: true
                  priority: 1
                descriptor:
                  agent_name: duplicate_agent
                  domain: general
                  display_name: Duplicate Agent
                  description: duplicate
                  required_fields: []
                  capabilities: []
                  routing_keywords: []
                  tool_names: []
                """
            ).strip()
            registry_dir.joinpath("a.yaml").write_text(payload + "\n", encoding="utf-8")
            registry_dir.joinpath("b.yaml").write_text(payload + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate agent descriptor"):
                AgentRegistryLoader(registry_dir).load()
