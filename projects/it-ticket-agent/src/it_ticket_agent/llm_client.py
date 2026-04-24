from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import httpx

from .observability import get_observability
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
        observability = get_observability()
        tool_names = [tool.get("function", {}).get("name") for tool in tools or []]
        with observability.start_span(
            name="llm.chat_completion",
            as_type="generation",
            input={
                "message_count": len(messages),
                "messages": messages,
                "tool_names": tool_names,
            },
            metadata={"provider": "openai_compatible"},
            model=self.settings.llm_model,
            model_parameters={"temperature": self.settings.llm_temperature},
        ) as generation:
            wire_api = str(getattr(self.settings, "llm_wire_api", "chat") or "chat").lower()
            if wire_api == "responses":
                primary = await self._chat_via_responses(messages, tools=tools)
                normalized = self._normalize_responses_message(primary)
            else:
                primary = await self._chat_via_chat_completions(messages, tools=tools)
                normalized = self._normalize_chat_message(primary)
                if self._should_fallback_to_responses(normalized, tools):
                    fallback = await self._chat_via_responses(messages, tools=tools)
                    normalized = self._normalize_responses_message(fallback)
            generation.update(output=normalized)
            return normalized

    async def _chat_via_chat_completions(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload: Dict[str, Any] = {
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

    async def _chat_via_responses(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        url = self.settings.llm_base_url.rstrip("/") + "/responses"
        instructions, input_items = self._messages_to_responses_input(messages)
        payload: Dict[str, Any] = {
            "model": self.settings.llm_model,
            "input": input_items,
            "temperature": self.settings.llm_temperature,
        }
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = [self._chat_tool_to_responses_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
            payload["stream"] = True
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if tools else "application/json",
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_sec) as client:
            if tools:
                return await self._stream_responses(client, url, headers=headers, payload=payload)
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def _stream_responses(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        text_parts: list[str] = []
        tool_calls: dict[str, dict[str, Any]] = {}
        item_to_call_id: dict[str, str] = {}
        latest_response: Dict[str, Any] = {}
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw or raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                event_type = str(event.get("type") or "")
                if event_type == "response.created":
                    latest_response = dict(event.get("response") or {})
                    continue
                if event_type == "response.output_text.delta":
                    delta = str(event.get("delta") or "")
                    if delta:
                        text_parts.append(delta)
                    continue
                if event_type == "response.output_item.added":
                    item = dict(event.get("item") or {})
                    item_type = str(item.get("type") or "")
                    if item_type in {"function_call", "tool_call"}:
                        item_id = str(item.get("id") or "")
                        call_id = str(item.get("call_id") or item.get("id") or "")
                        if call_id:
                            tool_calls[call_id] = {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": item.get("name"),
                                    "arguments": str(item.get("arguments") or ""),
                                },
                            }
                        if item_id and call_id:
                            item_to_call_id[item_id] = call_id
                    continue
                if event_type == "response.function_call_arguments.delta":
                    item_id = str(event.get("item_id") or "")
                    delta = str(event.get("delta") or "")
                    call_id = item_to_call_id.get(item_id, item_id)
                    if call_id and call_id in tool_calls:
                        tool_calls[call_id]["function"]["arguments"] = (
                            str(tool_calls[call_id]["function"].get("arguments") or "") + delta
                        )
                    continue
                if event_type == "response.function_call_arguments.done":
                    item_id = str(event.get("item_id") or "")
                    arguments = str(event.get("arguments") or "")
                    call_id = item_to_call_id.get(item_id, item_id)
                    if call_id and call_id in tool_calls and arguments:
                        tool_calls[call_id]["function"]["arguments"] = arguments
                    continue
                if event_type == "response.completed":
                    latest_response = dict(event.get("response") or latest_response)
                    continue

        return {
            "output_text": "".join(text_parts).strip(),
            "output": [],
            "tool_calls": list(tool_calls.values()),
            "response": latest_response,
        }

    @staticmethod
    def _chat_tool_to_responses_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
        function = dict(tool.get("function") or {})
        return {
            "type": "function",
            "name": function.get("name"),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
        }

    @staticmethod
    def _messages_to_responses_input(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        instructions = ""
        items: List[Dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system" and not instructions:
                instructions = content
                continue
            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(message.get("tool_call_id") or f"tool-call-{index}"),
                        "output": content,
                    }
                )
                continue
            normalized_role = role if role in {"user", "assistant", "developer"} else "user"
            items.append(
                {
                    "role": normalized_role,
                    "content": [{"type": "input_text", "text": content}],
                }
            )
        if not items:
            items.append({"role": "user", "content": [{"type": "input_text", "text": ""}]})
        return instructions, items

    @staticmethod
    def _normalize_chat_message(message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "content": str(message.get("content") or ""),
            "tool_calls": list(message.get("tool_calls") or []),
        }

    @staticmethod
    def _normalize_responses_message(data: Dict[str, Any]) -> Dict[str, Any]:
        raw_tool_calls = list(data.get("tool_calls") or [])
        if raw_tool_calls:
            return {
                "content": str(data.get("output_text") or "").strip(),
                "tool_calls": raw_tool_calls,
            }
        output = list(data.get("output") or [])
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in output:
            item_type = str(item.get("type") or "")
            if item_type in {"function_call", "tool_call"}:
                tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id"),
                        "type": "function",
                        "function": {
                            "name": item.get("name"),
                            "arguments": item.get("arguments") or "{}",
                        },
                    }
                )
                continue
            if item_type == "message":
                for content in list(item.get("content") or []):
                    if str(content.get("type") or "") in {"output_text", "text"}:
                        text = str(content.get("text") or "")
                        if text:
                            text_parts.append(text)
        top_level_text = data.get("output_text")
        if isinstance(top_level_text, str) and top_level_text.strip():
            text_parts.append(top_level_text.strip())
        return {
            "content": "\n".join(part for part in text_parts if part).strip(),
            "tool_calls": tool_calls,
        }

    @staticmethod
    def _should_fallback_to_responses(message: Dict[str, Any], tools: List[Dict[str, Any]] | None) -> bool:
        if not tools:
            return False
        content = str(message.get("content") or "").strip()
        tool_calls = list(message.get("tool_calls") or [])
        return not content and not tool_calls

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
