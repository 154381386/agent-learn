from __future__ import annotations

import importlib
import inspect

from ..mcp import MCPConnectionManager
from ..rag_client import RAGServiceClient
from ..settings import Settings
from .base import BaseDomainAgent
from .descriptors import AgentRegistry, AgentRegistryEntry


class AgentFactory:
    def __init__(
        self,
        *,
        settings: Settings,
        connection_manager: MCPConnectionManager,
        knowledge_client: RAGServiceClient,
    ) -> None:
        self.settings = settings
        self.connection_manager = connection_manager
        self.knowledge_client = knowledge_client

    def build_agents(self, registry: AgentRegistry) -> dict[str, BaseDomainAgent]:
        agents: dict[str, BaseDomainAgent] = {}
        for entry in registry.enabled_entries():
            descriptor = entry.descriptor
            agents[descriptor.agent_name] = self.create(entry)
        return agents

    def create(self, entry: AgentRegistryEntry) -> BaseDomainAgent:
        module_name, _, class_name = entry.implementation.rpartition(".")
        if not module_name or not class_name:
            raise ValueError(f"invalid agent implementation path: {entry.implementation}")
        module = importlib.import_module(module_name)
        agent_class = getattr(module, class_name, None)
        if agent_class is None:
            raise ValueError(f"agent implementation not found: {entry.implementation}")

        kwargs = self._resolve_constructor_kwargs(agent_class)
        agent = agent_class(**kwargs)
        if not isinstance(agent, BaseDomainAgent):
            raise ValueError(f"configured implementation is not a BaseDomainAgent: {entry.implementation}")
        agent.apply_descriptor(entry.descriptor)
        return agent

    def _resolve_constructor_kwargs(self, agent_class: type[BaseDomainAgent]) -> dict[str, object]:
        signature = inspect.signature(agent_class)
        kwargs: dict[str, object] = {}
        providers: dict[str, object] = {
            "settings": self.settings,
            "connection_manager": self.connection_manager,
            "knowledge_client": self.knowledge_client,
        }
        for name, parameter in signature.parameters.items():
            if name in providers:
                kwargs[name] = providers[name]
                continue
            if parameter.default is inspect.Signature.empty:
                raise ValueError(f"unsupported required constructor parameter: {agent_class.__name__}.{name}")
        return kwargs
