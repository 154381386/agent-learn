import json
from typing import Any, Dict, List, Optional

import httpx

from .schemas import RoutingDecision
from .settings import Settings


class OpenAICompatLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_api_key and self.settings.llm_base_url and self.settings.llm_model)

    async def route_ticket(
        self,
        message: str,
        known_facts: List[str],
        agent_catalog: List[Dict[str, str]],
    ) -> Optional[RoutingDecision]:
        if not self.enabled:
            return None

        system_prompt = (
            "你是企业 IT 工单系统里的路由器。"
            "请根据用户问题，从候选 Agent 中选择最合适的 1~3 个，并输出纯 JSON。"
            "不要输出 markdown，不要解释。"
        )
        user_prompt = {
            "message": message,
            "known_facts": known_facts,
            "agent_catalog": agent_catalog,
            "output_schema": {
                "intent": "string",
                "confidence": "0~1 float",
                "complexity_score": "0~1 float",
                "recommended_mode": "serial or parallel",
                "candidate_agents": ["agent-name"],
            },
        }
        content = await self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ]
        )
        if not content:
            return None

        try:
            payload = self._extract_json(content)
            return RoutingDecision(**payload)
        except Exception:
            return None

    async def render_final_response(
        self,
        user_message: str,
        rag_hit: bool,
        rag_answer: str,
        diagnosis: Dict[str, Any],
        action_result: Optional[Dict[str, Any]],
        knowledge_context: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        if not self.enabled:
            return None

        system_prompt = (
            "你是企业内部 IT 工单机器人 Robert。"
            "请根据给定事实生成面向用户的最终回复。"
            "要求：简洁、专业、先说结论，再说证据。"
            "如果提供了 knowledge_context，请在合适时引用文档标题，不要虚构来源。"
            "如果有审批/执行结果也要明确说明。"
        )
        user_payload = {
            "user_message": user_message,
            "rag_hit": rag_hit,
            "rag_answer": rag_answer,
            "diagnosis": diagnosis,
            "action_result": action_result,
            "knowledge_context": knowledge_context or [],
        }
        content = await self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
        )
        return content.strip() if content else None

    async def _chat(self, messages: List[Dict[str, str]]) -> str:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    @staticmethod
    def _extract_json(content: str) -> Dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end >= start:
            content = content[start : end + 1]
        return json.loads(content)
