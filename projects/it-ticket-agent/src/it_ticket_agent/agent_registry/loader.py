from __future__ import annotations

from pathlib import Path

import yaml

from ..agents.descriptors import AgentRegistry, AgentRegistryEntry
from ..settings import PROJECT_ROOT


class AgentRegistryLoader:
    def __init__(self, registry_path: str | Path) -> None:
        base_path = Path(registry_path)
        self.registry_path = base_path if base_path.is_absolute() else (PROJECT_ROOT / base_path).resolve()

    def load(self) -> AgentRegistry:
        if not self.registry_path.exists():
            raise ValueError(f"agent registry path does not exist: {self.registry_path}")
        if not self.registry_path.is_dir():
            raise ValueError(f"agent registry path is not a directory: {self.registry_path}")

        entries: list[AgentRegistryEntry] = []
        seen_names: set[str] = set()
        for path in sorted(self.registry_path.glob("*.y*ml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entry = AgentRegistryEntry.model_validate(payload)
            agent_name = entry.descriptor.agent_name
            if agent_name in seen_names:
                raise ValueError(f"duplicate agent descriptor found in registry: {agent_name}")
            seen_names.add(agent_name)
            entries.append(entry)

        if not entries:
            raise ValueError(f"no agent registry entries found under: {self.registry_path}")
        return AgentRegistry(entries=entries)
