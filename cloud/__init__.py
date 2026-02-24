#!/usr/bin/env python3
"""
cloud - A minimal Claude Code CLI wrapper for SSH-friendly use.

Usage:
    cloud                                          # interactive mode
    cloud "your prompt here"                       # one-shot
    echo "prompt" | cloud                          # one-shot via pipe
    cloud -c "follow-up"                           # continue last session
    cloud -c                                       # continue last session interactively
    cloud -r <session_id>                          # resume specific session
    cloud -m opus "prompt"                         # use a specific model
    cloud --dangerously-skip-permissions "prompt"  # skip all permission prompts

Settings (MCP servers, allowed tools, model defaults, etc.) are inherited
from Claude Code's own config at ~/.claude/settings.json.

Session IDs are saved to ~/.cc/last_session so -c always continues the
most recent conversation.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import re
import sys
import termios
from pathlib import Path
from types import SimpleNamespace

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
)

from . import input as cc_input
from .render import DIM, RESET, bubble_row_count, dots_paused, format_tool_use, print_recent_messages, print_user_bubble, setup_status_bar, stream_response, teardown_status_bar
from .session import load_last_session, save_session_id

async def _permission_callback(tool_name: str, tool_input: dict, context) -> object:
    """Ask the user whether to allow a tool call."""
    formatted = format_tool_use(SimpleNamespace(name=tool_name, input=tool_input))

    # Pause dots animation, clear line, show tool + prompt
    dots_paused.set()
    sys.stdout.write(f"\r\033[K{formatted}\n  Allow? [y/n] ")
    sys.stdout.flush()

    # Read a single keypress in cbreak mode
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    new = list(old)
    new[3] &= ~(termios.ICANON | termios.ECHO)
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    try:
        ch = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    dots_paused.clear()
    allowed = ch.lower() in (b"y", b"\r", b"\n")
    sys.stdout.write(("y" if allowed else "n") + "\n")
    sys.stdout.flush()

    if allowed:
        return PermissionResultAllow()
    return PermissionResultDeny(message="Denied by user")

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


async def run(prompt: str, options: ClaudeAgentOptions):
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        print()
        session_id, cost = await stream_response(client)
        if session_id:
            save_session_id(session_id)
        cost_str = f"${cost:.4f} | " if cost is not None else ""
        id_str = session_id[:8] if session_id else ""
        if cost_str or id_str:
            print(f"{DIM}({cost_str}{id_str}){RESET}")


async def interactive(options: ClaudeAgentOptions):
    current_options = options
    staged = ""
    should_exit = False
    total_cost = 0.0
    last_session_id: str | None = None

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
                    try:
                        session_id, cost = stream_task.result()
                    except Exception:
                        session_id, cost = None, None
                    if cost is not None:
                        total_cost += cost
                    if session_id:
                        last_session_id = session_id
                        save_session_id(session_id)
                        current_options = dataclasses.replace(current_options, resume=session_id)

    cost_str = f"${total_cost:.4f} | " if total_cost else ""
    id_str = last_session_id[:8] if last_session_id else ""
    if cost_str or id_str:
        print(f"{DIM}({cost_str}{id_str}){RESET}")


def build_options(args) -> tuple[ClaudeAgentOptions, str | None]:
    resume = None
    if args.continue_last:
        resume = load_last_session()
        if not resume:
            print("No previous session found.", file=sys.stderr)
            sys.exit(1)
    elif args.resume:
        resume = args.resume

    kwargs: dict = {"cwd": str(Path.cwd())}
    if args.dangerously_skip_permissions:
        kwargs["permission_mode"] = "bypassPermissions"
    else:
        kwargs["permission_mode"] = "default"
        kwargs["can_use_tool"] = _permission_callback
    if resume:
        kwargs["resume"] = resume
    if args.model:
        kwargs["model"] = args.model

    return ClaudeAgentOptions(**kwargs), resume


def _load_settings() -> dict:
    """Merge user + project settings the same way Claude Code does."""
    merged: dict = {}
    for path in [
        Path.home() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.local.json",
    ]:
        if path.exists():
            try:
                merged.update(json.loads(path.read_text()))
            except Exception:
                pass
    return merged


def _settings_summary(settings: dict, options: ClaudeAgentOptions) -> str:
    """Build a one-line summary of the active settings."""
    parts = []

    model = options.model or settings.get("model") or "default"
    parts.append(model)

    mcp = settings.get("mcpServers", {})
    if mcp:
        parts.append(f"{len(mcp)} MCP server{'s' if len(mcp) != 1 else ''}")

    plugins = [k.split("@")[0] for k, v in settings.get("enabledPlugins", {}).items() if v]
    if plugins:
        parts.append(", ".join(plugins))

    if settings.get("alwaysThinkingEnabled"):
        parts.append("thinking")

    return " · ".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Minimal Claude Code wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt (reads stdin if omitted)")
    parser.add_argument("-c", "--continue-last", action="store_true", help="Continue last session")
    parser.add_argument("-r", "--resume", metavar="ID", help="Resume a specific session")
    parser.add_argument("-m", "--model", metavar="MODEL", help="Model to use (e.g. sonnet, opus, haiku)")
    parser.add_argument("--dangerously-skip-permissions", action="store_true", help="Bypass all permission checks")
    args = parser.parse_args()

    settings = _load_settings()
    options, resume = build_options(args)

    setup_status_bar(_settings_summary(settings, options))

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
    finally:
        teardown_status_bar()
        sys.exit(0)


if __name__ == "__main__":
    main()
