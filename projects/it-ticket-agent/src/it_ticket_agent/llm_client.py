from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx

from .settings import Settings


class OpenAICompatToolLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_api_key and self.settings.llm_base_url and self.settings.llm_model)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]

    @staticmethod
    def extract_json(content: str) -> Dict[str, Any]:
        content = (content or "").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end >= start:
            content = content[start:end + 1]
        return json.loads(content)
