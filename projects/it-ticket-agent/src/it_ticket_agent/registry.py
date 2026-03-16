from dataclasses import dataclass
from typing import Dict, List

from .settings import Settings


@dataclass
class AgentDescriptor:
    name: str
    description: str
    endpoint: str
    transport: str
    timeout_sec: int
    keywords: List[str]


def build_registry(settings: Settings) -> Dict[str, AgentDescriptor]:
    return {
        "pod-analysis": AgentDescriptor(
            name="pod-analysis",
            description="Diagnose pod restarts, OOMKilled, probe failures",
            endpoint=settings.pod_agent_url,
            transport=settings.agent_transport,
            timeout_sec=8,
            keywords=["pod", "restart", "重启", "oom", "容器", "crashloop"],
        ),
        "root-cause": AgentDescriptor(
            name="root-cause",
            description="Correlate symptoms with recent changes and dependencies",
            endpoint=settings.rca_agent_url,
            transport=settings.agent_transport,
            timeout_sec=8,
            keywords=["发版", "变更", "发布", "异常", "回滚"],
        ),
        "network-diagnosis": AgentDescriptor(
            name="network-diagnosis",
            description="Diagnose connectivity, DNS and timeout issues",
            endpoint=settings.network_agent_url,
            transport=settings.agent_transport,
            timeout_sec=8,
            keywords=["网络", "dns", "timeout", "超时", "连通性"],
        ),
    }
