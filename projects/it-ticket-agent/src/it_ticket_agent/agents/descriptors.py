from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from ..runtime.contracts import AgentDescriptor


class AgentRoutingMetadata(BaseModel):
    enabled: bool = True
    priority: int = 100


class AgentRegistryEntry(BaseModel):
    version: int = 1
    enabled: bool = True
    implementation: str
    routing: AgentRoutingMetadata = Field(default_factory=AgentRoutingMetadata)
    descriptor: AgentDescriptor


class AgentRegistry(BaseModel):
    entries: List[AgentRegistryEntry] = Field(default_factory=list)

    def enabled_entries(self) -> list[AgentRegistryEntry]:
        return [entry for entry in self.entries if entry.enabled]

    def routable_entries(self) -> list[AgentRegistryEntry]:
        enabled = [entry for entry in self.enabled_entries() if entry.routing.enabled]
        return sorted(enabled, key=lambda entry: (entry.routing.priority, entry.descriptor.agent_name))
