"""
agent.py — FL Agent interactive loop using OpenRouter + MCP.

Reads AGENTS.md as the system prompt, connects to fl_mcp_server.py via
MCP stdio, and runs an agentic chat loop: user messages go to OpenRouter,
tool calls are forwarded to the MCP server.

Usage:
  python agent.py
  python agent.py --model anthropic/claude-opus-4-5
  python agent.py --model openai/openai/gpt-5.4-mini-mini

Environment variables:
  OPENROUTER_API_KEY   required
  OPENROUTER_MODEL     optional default model override (default: openai/openai/gpt-5.4-mini)
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT          = Path(__file__).parent
AGENTS_MD_PATH = _ROOT / "AGENTS.md"
SERVER_SCRIPT  = str(_ROOT / "fl_mcp_server.py")
VENV_PYTHON    = str(_ROOT / ".venv" / "Scripts" / "python.exe")

SYSTEM_PROMPT = AGENTS_MD_PATH.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------

def _openrouter_client():
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("ERROR: openai package not installed. Run: pip install openai")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENROUTER_API_KEY environment variable is not set.")

    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


# ---------------------------------------------------------------------------
# MCP tool conversion
# ---------------------------------------------------------------------------

def _mcp_tools_to_openai(tools) -> list:
    """Convert MCP tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]


def _assemble_streamed_tool_calls(partial_tool_calls: dict[int, dict[str, Any]]) -> list:
    """Convert streamed tool-call fragments into chat history tool calls."""
    assembled = []
    for index in sorted(partial_tool_calls):
        tool_call = partial_tool_calls[index]
        function = tool_call["function"]
        assembled.append(
            {
                "id": tool_call["id"] or f"call_{index}",
                "type": tool_call["type"] or "function",
                "function": {
                    "name": function["name"],
                    "arguments": function["arguments"] or "{}",
                },
            }
        )
    return assembled


def _stream_openrouter_response(llm, model: str, messages: list, openai_tools: list) -> tuple[dict, str | None]:
    """Stream the assistant response while rebuilding the final message payload."""
    print("\nAgent: thinking...", flush=True)

    stream = None
    content_parts: list[str] = []
    partial_tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    started_text = False

    try:
        stream = llm.chat.completions.create(
            model=model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            stream=True,
        )

        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta
            if delta is None:
                continue

            if delta.content:
                if not started_text:
                    print("Agent: ", end="", flush=True)
                    started_text = True
                print(delta.content, end="", flush=True)
                content_parts.append(delta.content)

            for tool_call in delta.tool_calls or []:
                partial = partial_tool_calls.setdefault(
                    tool_call.index,
                    {
                        "id": None,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if tool_call.id:
                    partial["id"] = tool_call.id
                if tool_call.type:
                    partial["type"] = tool_call.type
                if tool_call.function:
                    if tool_call.function.name:
                        partial["function"]["name"] += tool_call.function.name
                    if tool_call.function.arguments:
                        partial["function"]["arguments"] += tool_call.function.arguments
    finally:
        if started_text:
            print("\n", flush=True)
        if stream is not None and hasattr(stream, "close"):
            stream.close()

    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if partial_tool_calls:
        assistant_msg["tool_calls"] = _assemble_streamed_tool_calls(partial_tool_calls)

    return assistant_msg, finish_reason


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def run(model: str) -> None:
    python_exec = VENV_PYTHON if Path(VENV_PYTHON).exists() else sys.executable
    server_params = StdioServerParameters(command=python_exec, args=[SERVER_SCRIPT])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools    = (await session.list_tools()).tools
            openai_tools = _mcp_tools_to_openai(mcp_tools)
            llm          = _openrouter_client()

            print(f"\nFL Agent ready  [model: {model}]")
            print(f"Tools: {[t.name for t in mcp_tools]}")
            print("Type your music request, or 'quit' to exit.\n")

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            while True:
                # --- get user input ---
                try:
                    user_input = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye.")
                    break

                if user_input.lower() in ("quit", "exit", "q"):
                    print("Goodbye.")
                    break
                if not user_input:
                    continue

                turn_start = len(messages)
                messages.append({"role": "user", "content": user_input})

                # --- agentic loop: keep going until the LLM stops calling tools ---
                while True:
                    try:
                        assistant_msg, finish_reason = _stream_openrouter_response(
                            llm=llm,
                            model=model,
                            messages=messages,
                            openai_tools=openai_tools,
                        )
                        messages.append(assistant_msg)

                        tool_calls = assistant_msg.get("tool_calls") or []
                        if finish_reason == "tool_calls" and tool_calls:
                            # --- execute each tool call via MCP ---
                            for tc in tool_calls:
                                args = json.loads(tc["function"]["arguments"])
                                print(
                                    f"  -> {tc['function']['name']}({json.dumps(args)})",
                                    flush=True,
                                )

                                result = await session.call_tool(tc["function"]["name"], args)
                                tool_text = "\n".join(
                                    c.text for c in result.content if hasattr(c, "text")
                                )
                                print(f"  <- {tool_text[:300]}", flush=True)

                                messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tc["id"],
                                        "content": tool_text,
                                    }
                                )
                        else:
                            break
                        """
                                args = json.loads(tc["function"]["arguments"])
                            print(f"  -> {tc.function.name}({json.dumps(args)})")

                            result    = await session.call_tool(tc.function.name, args)
                            tool_text = "\n".join(
                                c.text for c in result.content if hasattr(c, "text")
                            )
                            print(f"  ← {tool_text[:300]}")

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": tool_text,
                            })
                        # loop back so the LLM can react to the tool results

                        """
                    except Exception as exc:
                        del messages[turn_start:]
                        print(f"ERROR: {exc}\n", flush=True)
                        break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FL Agent — OpenRouter + MCP")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENROUTER_MODEL", "openai/openai/gpt-5.4-mini"),
        help="OpenRouter model ID (default: openai/openai/gpt-5.4-mini)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.model))
