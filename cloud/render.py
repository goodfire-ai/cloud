import contextlib
import io
import os
import re
import sys
import textwrap
import threading
from types import SimpleNamespace

from claude_agent_sdk import (
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from .session import load_session_messages

DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

LIGHT_BLUE = "\033[94m"
BOLD = "\033[1m"
BOLD_OFF = "\033[22m"
ITALIC = "\033[3m"
ITALIC_OFF = "\033[23m"
UNDERLINE = "\033[4m"
UNDERLINE_OFF = "\033[24m"

DIFF_MAX_LINES = 40
BUBBLE_BG = "\033[48;5;235m"  # dark gray background

_MD_CODE = re.compile(r"`([^`\n]+)`")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_UNDERLINE = re.compile(r"__([^_\n]+)__")
_MD_ITALIC_STAR = re.compile(r"\*([^*\n]+)\*")
_MD_ITALIC_UNDER = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")


def render_markdown(text: str) -> str:
    """Minimal inline markdown → ANSI: `code`, **bold**, *italic*, __underline__."""
    text = _MD_CODE.sub(lambda m: f"{LIGHT_BLUE}{m.group(1)}{RESET}", text)
    text = _MD_BOLD.sub(lambda m: f"{BOLD}{m.group(1)}{BOLD_OFF}", text)
    text = _MD_UNDERLINE.sub(lambda m: f"{UNDERLINE}{m.group(1)}{UNDERLINE_OFF}", text)
    text = _MD_ITALIC_STAR.sub(lambda m: f"{ITALIC}{m.group(1)}{ITALIC_OFF}", text)
    text = _MD_ITALIC_UNDER.sub(lambda m: f"{ITALIC}{m.group(1)}{ITALIC_OFF}", text)
    return text


def _bubble_wrapped_lines(text: str) -> list[str]:
    """Return the wrapped lines that print_user_bubble would produce."""
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    max_content = min(width - 4, 72)
    wrapped: list[str] = []
    for line in text.split("\n"):
        wrapped.extend(textwrap.wrap(line, max_content) if line.strip() else [""])
    return wrapped


def bubble_row_count(text: str) -> int:
    """Number of terminal rows print_user_bubble(text) will occupy."""
    return 2 + len(_bubble_wrapped_lines(text))  # blank + content lines + blank


def print_user_bubble(text: str):
    """Print user message as a left-aligned background block."""
    print()
    for line in _bubble_wrapped_lines(text):
        print(f"  {BUBBLE_BG} {line} {RESET}")
    print()


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
            print(render_markdown(block["text"]), end="", flush=True)
        elif block.get("type") == "tool_use":
            b = SimpleNamespace(name=block.get("name", ""), input=block.get("input", {}))
            print(f"\n{format_tool_use(b)}", flush=True)
    print()


def print_recent_messages(session_id: str, max_lines: int = 200):
    """Print up to max_lines of recent session history."""
    exchanges = load_session_messages(session_id)
    if not exchanges:
        return

    # Render the last chunk of exchanges into a buffer
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for role, content in exchanges[-100:]:
            if role == "user":
                parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
                text = " ".join(parts).strip()
                if text:
                    print_user_bubble(text)
            else:
                render_content_blocks(content)

    lines = buf.getvalue().split("\n")
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    sys.stdout.write("\n".join(lines))
    if lines and lines[-1] != "":
        sys.stdout.write("\n")
    print(f"{DIM}{'─' * 40}{RESET}")


dots_paused = threading.Event()  # set by permission callback to suppress dots while prompting

_status_text: str = ""


def _term_rows() -> int:
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 24


def draw_status_bar() -> None:
    if not _status_text:
        return
    rows = _term_rows()
    sys.stdout.write(f"\0337\033[{rows};1H\033[K{DIM}  {_status_text}{RESET}\0338")
    sys.stdout.flush()


def setup_status_bar(text: str) -> None:
    global _status_text
    _status_text = text
    rows = _term_rows()
    # DECSTBM always jumps cursor to (1,1), so reposition to bottom of scroll region after.
    sys.stdout.write(f"\033[1;{rows - 1}r\033[{rows - 1};1H")
    sys.stdout.flush()
    draw_status_bar()


def teardown_status_bar() -> None:
    global _status_text
    rows = _term_rows()
    sys.stdout.write(f"\0337\033[{rows};1H\033[K\0338\033[r")
    sys.stdout.flush()
    _status_text = ""


def waiting_dots_thread(stop: threading.Event):
    """Show a simple ... animation in a thread until stop is set."""
    frames = [".", "..", "..."]
    i = 0
    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()
    while not stop.wait(0.4):
        if not dots_paused.is_set():
            sys.stdout.write(f"\r{DIM}{frames[i % 3]:<3}  c:cancel{RESET}")
            sys.stdout.flush()
        i += 1
    sys.stdout.write("\r\033[K\033[?25h")  # clear line, restore cursor
    sys.stdout.flush()
    draw_status_bar()


async def stream_response(client: ClaudeSDKClient, output: list | None = None) -> tuple[str | None, float | None]:
    """Stream one response turn. Returns (session_id, cost_usd).
    If output list is provided, printed text chunks are appended to it."""
    session_id = None
    total_cost: float | None = None
    stop = threading.Event()
    dots = threading.Thread(target=waiting_dots_thread, args=(stop,), daemon=True)
    dots.start()

    # Buffer incoming text chunks so markdown spans aren't split across flush boundaries.
    # We render complete lines eagerly; any trailing partial line waits for the next chunk.
    text_buf: list[str] = []

    def _flush(force: bool = False) -> None:
        if not text_buf:
            return
        blob = "".join(text_buf)
        text_buf.clear()
        if force:
            if output is not None:
                output.append(blob)
            print(render_markdown(blob), end="", flush=True)
            return
        # Render up to (and including) the last newline; hold back any partial final line.
        nl = blob.rfind("\n")
        if nl == -1:
            text_buf.append(blob)
        else:
            complete = blob[: nl + 1]
            if output is not None:
                output.append(complete)
            print(render_markdown(complete), end="", flush=True)
            if nl + 1 < len(blob):
                text_buf.append(blob[nl + 1 :])

    def stop_dots():
        if not stop.is_set():
            stop.set()
            dots.join()

    try:
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.error:
                    _flush(force=True)
                    print(f"\n{RED}Error: {msg.error}{RESET}", file=sys.stderr)
                    continue
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        stop_dots()
                        text_buf.append(block.text)
                        _flush()
                    elif isinstance(block, ToolUseBlock):
                        stop_dots()
                        _flush(force=True)
                        formatted = format_tool_use(block)
                        if output is not None:
                            output.append(f"\n{formatted}\n")
                        print(f"\n{formatted}", flush=True)

            elif isinstance(msg, ResultMessage):
                _flush(force=True)
                if msg.is_error:
                    print(f"\n{RED}Error: {msg.result}{RESET}", file=sys.stderr)
                    return session_id, total_cost
                session_id = msg.session_id
                total_cost = msg.total_cost_usd
    finally:
        _flush(force=True)
        stop_dots()

    print()
    return session_id, total_cost
