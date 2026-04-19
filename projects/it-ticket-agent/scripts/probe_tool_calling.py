from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List

import httpx

from it_ticket_agent.llm_client import OpenAICompatToolLLM
from it_ticket_agent.settings import Settings


def build_dummy_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "echo_service_status",
                "description": "Return the current status of a service. Use this tool instead of answering from memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string", "description": "service name"},
                    },
                    "required": ["service"],
                },
            },
        }
    ]


def build_messages(service: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You are testing function calling support. "
                "You MUST call the provided function first. "
                "Do not answer directly without calling the function."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Check the status of service '{service}'. "
                "Use the function call immediately; do not explain."
            ),
        },
    ]


async def call_chat_completions(
    settings: Settings,
    *,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    force_tool_choice: bool,
) -> Dict[str, Any]:
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature,
        "tools": tools,
        "tool_choice": (
            {
                "type": "function",
                "function": {"name": tools[0]["function"]["name"]},
            }
            if force_tool_choice
            else "auto"
        ),
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def call_responses(
    settings: Settings,
    *,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    force_tool_choice: bool,
) -> Dict[str, Any]:
    url = settings.llm_base_url.rstrip("/") + "/responses"
    client = OpenAICompatToolLLM(settings)
    instructions, input_items = client._messages_to_responses_input(messages)
    payload: Dict[str, Any] = {
        "model": settings.llm_model,
        "input": input_items,
        "temperature": settings.llm_temperature,
        "tools": [client._chat_tool_to_responses_tool(tool) for tool in tools],
        "parallel_tool_calls": True,
        "tool_choice": (
            {
                "type": "function",
                "name": tools[0]["function"]["name"],
            }
            if force_tool_choice
            else "auto"
        ),
    }
    if instructions:
        payload["instructions"] = instructions
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout_sec) as client_http:
        response = await client_http.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def extract_chat_tool_calls(data: Dict[str, Any]) -> list[dict[str, Any]]:
    message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    return list(message.get("tool_calls") or [])


def extract_responses_tool_calls(data: Dict[str, Any]) -> list[dict[str, Any]]:
    output = list(data.get("output") or [])
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        item_type = str(item.get("type") or "")
        if item_type in {"function_call", "tool_call"}:
            tool_calls.append(item)
    return tool_calls


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe whether the configured LLM returns tool_calls.")
    parser.add_argument("--service", default="checkout-service", help="Service name used in the probe prompt")
    parser.add_argument("--expect-tool-call", action="store_true", help="Exit non-zero if no tool call is returned")
    parser.add_argument("--force-tool-choice", action="store_true", help="Force a specific function instead of tool_choice=auto")
    parser.add_argument(
        "--mode",
        choices=["chat", "responses", "both", "normalized"],
        default="both",
        help="Which path to probe",
    )
    args = parser.parse_args()

    settings = Settings()
    client = OpenAICompatToolLLM(settings)
    print(f"llm_enabled={client.enabled}")
    print(f"model={settings.llm_model}")
    print(f"base_url={settings.llm_base_url}")
    if not client.enabled:
        print("LLM is not enabled by current env config.")
        return 2

    messages = build_messages(args.service)
    tools = build_dummy_tools()
    failures = 0

    if args.mode in {"chat", "both"}:
        chat_raw = await call_chat_completions(
            settings,
            messages=messages,
            tools=tools,
            force_tool_choice=args.force_tool_choice,
        )
        chat_calls = extract_chat_tool_calls(chat_raw)
        print("--- raw chat/completions response ---")
        print(json.dumps(chat_raw, ensure_ascii=False, indent=2))
        print(f"chat_tool_calls_count={len(chat_calls)}")
        if args.expect_tool_call and not chat_calls:
            failures += 1

    if args.mode in {"responses", "both"}:
        responses_raw = await call_responses(
            settings,
            messages=messages,
            tools=tools,
            force_tool_choice=args.force_tool_choice,
        )
        responses_calls = extract_responses_tool_calls(responses_raw)
        print("--- raw responses response ---")
        print(json.dumps(responses_raw, ensure_ascii=False, indent=2))
        print(f"responses_tool_calls_count={len(responses_calls)}")
        if args.expect_tool_call and not responses_calls:
            failures += 1

    if args.mode == "normalized":
        message = await client.chat(messages, tools=tools)
        content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        print("--- normalized response ---")
        print(f"content={content!r}")
        print(f"tool_calls_count={len(tool_calls) if isinstance(tool_calls, list) else 0}")
        if isinstance(tool_calls, list):
            print(json.dumps(tool_calls, ensure_ascii=False, indent=2))
        if args.expect_tool_call and (not isinstance(tool_calls, list) or not tool_calls):
            failures += 1

    if failures:
        print("expected tool_calls but got none on one or more probed paths", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
