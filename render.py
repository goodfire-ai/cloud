import sys
import threading
from types import SimpleNamespace

from claude_agent_sdk import (
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from session import load_session_messages

DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
BLUE = "\033[34;1m"
RESET = "\033[0m"

DIFF_MAX_LINES = 40


def format_diff(old: str, new: str) -> str:
    """Red/green diff of old → new."""
    lines = [f"{RED}- {line}{RESET}" for line in old.splitlines()]
    lines += [f"{GREEN}+ {line}{RESET}" for line in new.splitlines()]
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
            parts = [header] + [
                format_diff(e.get("old_string", ""), e.get("new_string", ""))
                for e in edits
            ]
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


def render_content_blocks(content: list[dict]):
    """Render a list of raw content block dicts (from session history)."""
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            print(block["text"], end="", flush=True)
        elif block.get("type") == "tool_use":
            b = SimpleNamespace(name=block.get("name", ""), input=block.get("input", {}))
            print(f"\n{format_tool_use(b)}", flush=True)
    print()


def print_recent_messages(session_id: str, n: int = 2):
    """Print the last n user/assistant exchange pairs from a session."""
    exchanges = load_session_messages(session_id)
    if not exchanges:
        return

    for role, content in exchanges[-n * 2:]:
        if role == "user":
            parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
            text = " ".join(parts).strip()
            if text:
                print(f"{BLUE}>{RESET} {text}")
        else:
            render_content_blocks(content)

    print(f"{DIM}{'─' * 40}{RESET}")


def waiting_dots_thread(stop: threading.Event):
    """Show a simple ... animation in a thread until stop is set."""
    frames = [".", "..", "..."]
    i = 0
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()
    while not stop.wait(0.4):
        sys.stdout.write(f"\r{DIM}{frames[i % 3]:<3}{RESET}")
        sys.stdout.flush()
        i += 1
    sys.stdout.write("\r   \r\033[?25h")  # clear dots, restore cursor
    sys.stdout.flush()


async def stream_response(client: ClaudeSDKClient) -> str | None:
    """Stream one response turn. Returns session_id if present."""
    session_id = None
    stop = threading.Event()
    dots = threading.Thread(target=waiting_dots_thread, args=(stop,), daemon=True)
    dots.start()

    def stop_dots():
        if not stop.is_set():
            stop.set()
            dots.join()

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            if msg.error:
                print(f"\n{RED}Error: {msg.error}{RESET}", file=sys.stderr)
                continue
            for block in msg.content:
                if isinstance(block, TextBlock):
                    stop_dots()
                    print(block.text, end="", flush=True)
                elif isinstance(block, ToolUseBlock):
                    stop_dots()
                    print(f"\n{format_tool_use(block)}", flush=True)

        elif isinstance(msg, ResultMessage):
            if msg.is_error:
                print(f"\n{RED}Error: {msg.result}{RESET}", file=sys.stderr)
                return session_id
            session_id = msg.session_id
            if msg.total_cost_usd is not None:
                print(f"\n{DIM}(${msg.total_cost_usd:.4f} | {session_id[:8]}){RESET}")

    stop_dots()
    print()
    return session_id
