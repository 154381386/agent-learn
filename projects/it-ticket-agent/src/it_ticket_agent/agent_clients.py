import asyncio
from typing import Any, Dict, List

import httpx

from .registry import AgentDescriptor
from .schemas import AgentResult, SuggestedAction, TaskPackage, model_to_dict


class LocalAgentRuntime:
    async def run(self, agent_name: str, task: TaskPackage) -> AgentResult:
        message = task.symptom.lower()
        knowledge_evidence = []
        if task.knowledge_context:
            first_hit = task.knowledge_context[0]
            knowledge_evidence.append(
                f"知识库参考：{first_hit.get('title', '未命名文档')} / {first_hit.get('section', '摘要')}"
            )

        if agent_name == "pod-analysis":
            evidence = [
                "Pod 重启次数在 10 分钟内超过阈值",
                "最近一次状态包含 OOMKilled",
                *knowledge_evidence,
            ]
            actions = []
            if "发版" in message or "发布" in message:
                actions.append(
                    SuggestedAction(
                        action="rollback",
                        risk="high",
                        reason="Pod 异常与最近发布相关，建议先回滚止血",
                        params={"service": task.service, "target_version": "v2.3.0"},
                    )
                )
            return AgentResult(
                agent=agent_name,
                conclusion="疑似 OOMKilled 导致 Pod 重启",
                confidence=0.86,
                evidence=evidence,
                suggested_actions=actions,
            )

        if agent_name == "root-cause":
            evidence = [
                "最近 2 小时内存在服务发布记录",
                "变更涉及数据库连接池参数",
                *knowledge_evidence,
            ]
            return AgentResult(
                agent=agent_name,
                conclusion="故障可能与最近版本变更相关",
                confidence=0.74,
                evidence=evidence,
                suggested_actions=[],
            )

        if agent_name == "network-diagnosis":
            return AgentResult(
                agent=agent_name,
                conclusion="网络连通性正常，可暂时排除网络因素",
                confidence=0.92,
                evidence=["DNS 查询成功", "下游健康检查无明显异常", *knowledge_evidence],
                suggested_actions=[],
            )

        return AgentResult(
            agent=agent_name,
            conclusion="暂无诊断结论",
            confidence=0.2,
            evidence=knowledge_evidence,
            suggested_actions=[],
        )


class AgentClient:
    def __init__(self) -> None:
        self.local_runtime = LocalAgentRuntime()

    async def run_agent(self, descriptor: AgentDescriptor, task: TaskPackage) -> Dict[str, Any]:
        if descriptor.transport == "http":
            async with httpx.AsyncClient(timeout=descriptor.timeout_sec) as client:
                response = await client.post(descriptor.endpoint, json=model_to_dict(task))
                response.raise_for_status()
                return response.json()

        result = await self.local_runtime.run(descriptor.name, task)
        return model_to_dict(result)

    async def run_many(self, descriptors: List[AgentDescriptor], task: TaskPackage) -> List[Dict[str, Any]]:
        coroutines = [self.run_agent(descriptor, task) for descriptor in descriptors]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        normalized: List[Dict[str, Any]] = []
        for descriptor, result in zip(descriptors, results):
            if isinstance(result, Exception):
                normalized.append(
                    {
                        "agent": descriptor.name,
                        "conclusion": "Agent 调用失败",
                        "confidence": 0.0,
                        "evidence": [str(result)],
                        "suggested_actions": [],
                    }
                )
                continue
            normalized.append(result)
        return normalized
