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
    cc -s                                   # pick session with fzf
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
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

import input as cc_input
from render import print_recent_messages, stream_response
from session import load_config, load_last_session, pick_session_fzf, save_session_id

PLAN_PROMPT = """
Before taking any action, you MUST output a numbered plan of what you intend to do.
Format it as:
  Plan:
  1. ...
  2. ...
  ...
Then execute the plan step by step.
""".strip()


async def run(prompt: str, options: ClaudeAgentOptions):
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        print()
        session_id = await stream_response(client)
        if session_id:
            save_session_id(session_id)


async def interactive(options: ClaudeAgentOptions):
    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                prompt = await cc_input.read_input()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not prompt.strip():
                continue

            await client.query(prompt)
            print()
            session_id = await stream_response(client)
            if session_id:
                save_session_id(session_id)


def build_options(args, config: dict) -> tuple[ClaudeAgentOptions, str | None]:
    append_parts = []
    if config.get("system_prompt"):
        append_parts.append(config["system_prompt"])
    if args.plan:
        append_parts.append(PLAN_PROMPT)

    resume = None
    if args.sessions:
        resume = pick_session_fzf()
    elif args.continue_last:
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
    kwargs["permission_mode"] = "bypassPermissions" if args.yes else "acceptEdits"
    if resume:
        kwargs["resume"] = resume
    if config.get("mcp_servers"):
        kwargs["mcp_servers"] = config["mcp_servers"]
    if config.get("allowed_tools"):
        kwargs["allowed_tools"] = config["allowed_tools"]

    return ClaudeAgentOptions(**kwargs), resume


def main():
    parser = argparse.ArgumentParser(
        description="Minimal Claude Code wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt (reads stdin if omitted)")
    parser.add_argument("-c", "--continue-last", action="store_true", help="Continue last session")
    parser.add_argument("-r", "--resume", metavar="ID", help="Resume specific session")
    parser.add_argument("-s", "--sessions", action="store_true", help="Pick session with fzf")
    parser.add_argument("-p", "--plan", action="store_true", help="Plan before acting")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip permission prompts")
    args = parser.parse_args()

    cc_input.setup()
    config = load_config()
    options, resume = build_options(args, config)

    if resume:
        print_recent_messages(resume)

    try:
        if args.prompt:
            asyncio.run(run(args.prompt, options))
        elif not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            if not prompt:
                print("Error: no prompt provided", file=sys.stderr)
                sys.exit(1)
            asyncio.run(run(prompt, options))
        else:
            asyncio.run(interactive(options))
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
