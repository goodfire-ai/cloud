#!/usr/bin/env python3
"""
cc - A minimal Claude Code CLI wrapper for SSH-friendly use.

Usage:
    cc                                      # interactive mode
    cc "your prompt here"                   # one-shot
    echo "prompt" | cc                      # one-shot via pipe
    cc -c "follow-up"                       # continue last session
    cc -c                                   # continue last session interactively
    cc -r <session_id> "prompt"             # resume specific session
    cc -p "prompt"                          # plan mode (thinks before acting)
    cc -y "prompt"                          # skip all permission prompts
    cc -y -p "build me a thing"             # combine flags

Config (~/.cc/config.json):
    {
        "mcp_servers": {
            "my-server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@my/mcp-server"]
            }
        },
        "system_prompt": "You are working in a Python monorepo...",
        "allowed_tools": ["Bash", "Read", "Write", "Edit"]
    }

Session IDs are saved to ~/.cc/last_session so -c always continues the
most recent conversation.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

CONFIG_DIR = Path.home() / ".cc"
CONFIG_FILE = CONFIG_DIR / "config.json"
LAST_SESSION_FILE = CONFIG_DIR / "last_session"

DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

DIFF_MAX_LINES = 40

PLAN_PROMPT = """
Before taking any action, you MUST output a numbered plan of what you intend to do.
Format it as:
  Plan:
  1. ...
  2. ...
  ...
Then execute the plan step by step.
""".strip()


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_session_id(session_id: str):
    CONFIG_DIR.mkdir(exist_ok=True)
    LAST_SESSION_FILE.write_text(session_id)


def load_last_session() -> str | None:
    if LAST_SESSION_FILE.exists():
        return LAST_SESSION_FILE.read_text().strip() or None
    return None


def format_diff(old: str, new: str) -> str:
    """Red/green diff of old → new."""
    lines = []
    for line in old.splitlines():
        lines.append(f"{RED}- {line}{RESET}")
    for line in new.splitlines():
        lines.append(f"{GREEN}+ {line}{RESET}")
    if len(lines) > DIFF_MAX_LINES:
        truncated = len(lines) - DIFF_MAX_LINES
        lines = lines[:DIFF_MAX_LINES]
        lines.append(f"{DIM}  ... ({truncated} more lines){RESET}")
    return "\n".join(lines)


def format_new_content(content: str) -> str:
    """Green lines for new file content."""
    lines = content.splitlines()
    shown = [f"{GREEN}+ {line}{RESET}" for line in lines[:DIFF_MAX_LINES]]
    if len(lines) > DIFF_MAX_LINES:
        shown.append(f"{DIM}  ... ({len(lines) - DIFF_MAX_LINES} more lines){RESET}")
    return "\n".join(shown)


def format_tool_use(block: ToolUseBlock) -> str:
    """Format a tool call with optional diff."""
    name = block.name
    inp = block.input

    match name:
        case "Bash":
            return f"{DIM}[Bash] {inp.get('command', '')[:80]}{RESET}"
        case "Edit":
            path = inp.get("file_path", "")
            header = f"{DIM}[Edit] {path}{RESET}"
            old = inp.get("old_string", "")
            new = inp.get("new_string", "")
            if old or new:
                return f"{header}\n{format_diff(old, new)}"
            return header
        case "Write":
            path = inp.get("file_path", "")
            header = f"{DIM}[Write] {path}{RESET}"
            content = inp.get("content", "")
            if content:
                return f"{header}\n{format_new_content(content)}"
            return header
        case "MultiEdit":
            path = inp.get("file_path", "")
            header = f"{DIM}[MultiEdit] {path}{RESET}"
            edits = inp.get("edits", [])
            if not edits:
                return header
            parts = [header]
            for edit in edits:
                parts.append(format_diff(
                    edit.get("old_string", ""),
                    edit.get("new_string", ""),
                ))
            return "\n".join(parts)
        case "Read":
            return f"{DIM}[Read] {inp.get('file_path', '')}{RESET}"
        case "Glob":
            return f"{DIM}[Glob] {inp.get('pattern', '')}{RESET}"
        case "Grep":
            return f"{DIM}[Grep] {inp.get('pattern', '')} in {inp.get('path', '.')}{RESET}"
        case "WebSearch":
            return f"{DIM}[WebSearch] {inp.get('query', '')}{RESET}"
        case _:
            detail = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:2])
            return f"{DIM}[{name}] {detail}{RESET}"


async def stream_response(client: ClaudeSDKClient) -> str | None:
    """Stream one response turn. Returns session_id if present."""
    session_id = None
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            assert not msg.error, f"agent error: {msg.error}"
            for block in msg.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
                elif isinstance(block, ToolUseBlock):
                    print(f"\n{format_tool_use(block)}", flush=True)

        elif isinstance(msg, ResultMessage):
            assert not msg.is_error, f"agent error: {msg.result}"
            session_id = msg.session_id
            if msg.total_cost_usd is not None:
                print(f"\n{DIM}(${msg.total_cost_usd:.4f} | {session_id[:8]}){RESET}")

    print()
    return session_id


async def run(prompt: str, options: ClaudeAgentOptions):
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        session_id = await stream_response(client)
        if session_id:
            save_session_id(session_id)


async def interactive(options: ClaudeAgentOptions):
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                prompt = input(f"{DIM}>{RESET} ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not prompt.strip():
                continue

            await client.query(prompt)
            session_id = await stream_response(client)
            if session_id:
                save_session_id(session_id)


def build_options(args, config: dict) -> ClaudeAgentOptions:
    append_parts = []
    if config.get("system_prompt"):
        append_parts.append(config["system_prompt"])
    if args.plan:
        append_parts.append(PLAN_PROMPT)

    resume = None
    if args.continue_last:
        resume = load_last_session()
        if not resume:
            print("No previous session found.", file=sys.stderr)
            sys.exit(1)
    elif args.resume:
        resume = args.resume

    kwargs: dict = {"cwd": str(Path.cwd())}

    if append_parts:
        kwargs["system_prompt"] = {
            "type": "preset",
            "preset": "claude_code",
            "append": "\n\n".join(append_parts),
        }
    if args.yes:
        kwargs["permission_mode"] = "bypassPermissions"
    else:
        kwargs["permission_mode"] = "acceptEdits"
    if resume:
        kwargs["resume"] = resume
    if config.get("mcp_servers"):
        kwargs["mcp_servers"] = config["mcp_servers"]
    if config.get("allowed_tools"):
        kwargs["allowed_tools"] = config["allowed_tools"]

    return ClaudeAgentOptions(**kwargs)


def main():
    parser = argparse.ArgumentParser(
        description="Minimal Claude Code wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt (reads stdin if omitted)")
    parser.add_argument("-c", "--continue-last", action="store_true", help="Continue last session")
    parser.add_argument("-r", "--resume", metavar="ID", help="Resume specific session")
    parser.add_argument("-p", "--plan", action="store_true", help="Plan before acting")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip permission prompts")
    args = parser.parse_args()

    config = load_config()
    options = build_options(args, config)

    try:
        if args.prompt:
            asyncio.run(run(args.prompt, options))
        elif not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            assert prompt, "No prompt provided"
            asyncio.run(run(prompt, options))
        else:
            asyncio.run(interactive(options))
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
