#!/usr/bin/env python3
"""
cloud - A minimal Claude Code CLI wrapper for SSH-friendly use.

Usage:
    cloud                                      # interactive mode
    cloud "your prompt here"                   # one-shot
    echo "prompt" | cloud                      # one-shot via pipe
    cloud -c "follow-up"                       # continue last session
    cloud -c                                   # continue last session interactively
    cloud -r <session_id> "prompt"             # resume specific session
    cloud -s                                   # pick session with fzf
    cloud -p "prompt"                          # plan mode (thinks before acting)
    cloud -y "prompt"                          # skip all permission prompts
    cloud -y -p "build me a thing"             # combine flags

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
import dataclasses
import os
import re
import sys
from pathlib import Path


from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

import input as cc_input
from render import bubble_row_count, print_recent_messages, print_user_bubble, stream_response
from session import load_config, load_last_session, pick_session_fzf, save_session_id

_ANSI_RE = re.compile(r"\033\[[^a-zA-Z]*[a-zA-Z]")


def _count_rows(text: str) -> int:
    """Count terminal rows consumed by text, respecting newlines and line wrapping."""
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    clean = _ANSI_RE.sub("", text)
    rows = 0
    col = 0
    for ch in clean:
        if ch == "\n":
            rows += 1
            col = 0
        else:
            col += 1
            if col >= width:
                rows += 1
                col = 0
    return rows


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
    current_options = options
    staged = ""
    should_exit = False

    while not should_exit:
        async with ClaudeSDKClient(options=current_options) as client:
            while True:
                try:
                    prompt = await cc_input.read_input(initial=staged)
                    staged = ""
                except (EOFError, KeyboardInterrupt):
                    should_exit = True
                    break

                if not prompt.strip():
                    continue

                print_user_bubble(prompt)
                await client.query(prompt)
                print()

                output: list[str] = []
                stream_task = asyncio.create_task(stream_response(client, output=output))
                cancel_task = asyncio.create_task(cc_input.read_cancel_key())

                done, pending = await asyncio.wait(
                    [stream_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                if cancel_task in done:
                    # Erase bubble + streaming output precisely so resend shows a fresh bubble
                    rows_up = bubble_row_count(prompt) + 1 + _count_rows("".join(output))
                    if rows_up > 0:
                        sys.stdout.write(f"\033[{rows_up}A")
                    sys.stdout.write("\r\033[J")
                    sys.stdout.flush()
                    staged = prompt
                    last = load_last_session()
                    if last:
                        current_options = dataclasses.replace(current_options, resume=last)
                    break
                else:
                    # Normal completion
                    try:
                        session_id = stream_task.result()
                    except Exception:
                        session_id = None
                    if session_id:
                        save_session_id(session_id)
                        # Keep resume pointer up to date for any future reconnect
                        current_options = dataclasses.replace(current_options, resume=session_id)


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
