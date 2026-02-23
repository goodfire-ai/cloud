"""
Custom terminal input with Kitty keyboard protocol support.

Supports: Shift+Enter (newline), Alt+Backspace (delete word),
multiline editing, paste. Works over SSH.
"""

import asyncio
import math
import os
import re
import sys
import termios
import time
import tty

_ANSI_RE = re.compile(r"\033\[[^m]*m")

DIM = "\033[2m"
BLUE = "\033[34;1m"
RESET = "\033[0m"
CLEAR_TO_END = "\033[J"
ENABLE_KITTY = "\033[>1u"
DISABLE_KITTY = "\033[<u"

_original_termios: list | None = None


def setup() -> None:
    pass


def _enter_raw():
    global _original_termios
    fd = sys.stdin.fileno()
    _original_termios = termios.tcgetattr(fd)
    tty.setraw(fd)
    sys.stdout.write(ENABLE_KITTY)
    sys.stdout.flush()


def _exit_raw():
    sys.stdout.write(DISABLE_KITTY)
    sys.stdout.flush()
    if _original_termios is not None:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _original_termios)


def _drain_nonblocking(fd: int) -> bytes:
    """Read all immediately available bytes from fd."""
    old = termios.tcgetattr(fd)
    new = list(old)
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    data = b""
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        data += chunk
    termios.tcsetattr(fd, termios.TCSANOW, old)
    return data


async def _read_bytes() -> bytes:
    """Wait for stdin to be readable, then drain all available bytes."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[bytes] = loop.create_future()

    def on_readable():
        loop.remove_reader(sys.stdin.fileno())
        fd = sys.stdin.fileno()
        data = _drain_nonblocking(fd)
        # If we got a bare escape, wait briefly for follow-up bytes (e.g. alt+backspace)
        if data == b"\x1b":
            time.sleep(0.02)
            more = _drain_nonblocking(fd)
            if more:
                data += more
        fut.set_result(data)

    loop.add_reader(sys.stdin.fileno(), on_readable)
    return await fut


def _parse_key(data: bytes) -> str:
    # Kitty protocol: CSI <keycode> ; <modifiers> u
    if data.startswith(b"\x1b[") and data.endswith(b"u"):
        inner = data[2:-1].decode()
        parts = inner.split(";")
        keycode = int(parts[0])
        mods = int(parts[1]) - 1 if len(parts) > 1 else 0
        shift = bool(mods & 1)
        alt   = bool(mods & 2)
        ctrl  = bool(mods & 4)
        if keycode == 13:
            if shift: return "shift-enter"
            if alt:   return "alt-enter"
            return "enter"
        if keycode == 127:
            if alt or ctrl: return "alt-backspace"
            return "backspace"

    if data == b"\r" or data == b"\n":
        return "enter"
    if data == b"\x7f" or data == b"\x08":
        return "backspace"
    if data == b"\x1b\x7f" or data == b"\x1b\x08":
        return "alt-backspace"
    if data == b"\x17":
        return "alt-backspace"  # ctrl+w
    if data == b"\x03":
        return "ctrl-c"
    if data == b"\x04":
        return "ctrl-d"
    if data == b"\x1b[C":
        return "right"
    if data == b"\x1b[D":
        return "left"
    if data == b"\x1b\r" or data == b"\x1b\n":
        return "alt-enter"

    try:
        text = data.decode("utf-8")
        if all(c.isprintable() or c == "\n" for c in text):
            return f"text:{text}"
    except UnicodeDecodeError:
        pass

    return "unknown"


class LineEditor:
    def __init__(self):
        self.buf: list[str] = []
        self.cursor = 0

    @property
    def text(self) -> str:
        return "".join(self.buf)

    def insert(self, s: str):
        for ch in s:
            self.buf.insert(self.cursor, ch)
            self.cursor += 1

    def backspace(self):
        if self.cursor > 0:
            self.cursor -= 1
            del self.buf[self.cursor]

    def delete_word_back(self):
        if self.cursor == 0:
            return
        while self.cursor > 0 and self.buf[self.cursor - 1] in (" ", "\n"):
            self.cursor -= 1
            del self.buf[self.cursor]
        while self.cursor > 0 and self.buf[self.cursor - 1] not in (" ", "\n"):
            self.cursor -= 1
            del self.buf[self.cursor]

    def move_left(self):
        if self.cursor > 0:
            self.cursor -= 1

    def move_right(self):
        if self.cursor < len(self.buf):
            self.cursor += 1


def _visual_rows(line: str, prefix_len: int, width: int) -> int:
    """Number of terminal rows a line occupies given its prefix and terminal width."""
    return max(1, math.ceil((prefix_len + len(line)) / width))


def _render(editor: LineEditor, prompt: str, prev_rows: int = 1) -> int:
    """Redraw prompt+buffer. Returns total visual rows now displayed."""
    width = os.get_terminal_size().columns
    prompt_visible = len(_ANSI_RE.sub("", prompt))

    text = editor.text
    before_cursor = text[:editor.cursor]
    lines = text.split("\n")
    cursor_line_idx = before_cursor.count("\n")
    cursor_col = len(before_cursor.split("\n")[-1])

    # Calculate total visual rows and rows above cursor
    total_rows = sum(
        _visual_rows(line, prompt_visible if i == 0 else 2, width)
        for i, line in enumerate(lines)
    )
    rows_above_cursor = sum(
        _visual_rows(lines[i], prompt_visible if i == 0 else 2, width)
        for i in range(cursor_line_idx)
    )
    # Add wrapped rows within the cursor's own line above the cursor position
    prefix_len = prompt_visible if cursor_line_idx == 0 else 2
    rows_above_cursor += (prefix_len + cursor_col) // width

    # Move back to top of previous render
    if prev_rows > 1:
        sys.stdout.write(f"\033[{prev_rows - 1}A")
    sys.stdout.write(f"\r{CLEAR_TO_END}")

    # Draw all lines
    for i, line in enumerate(lines):
        if i == 0:
            sys.stdout.write(f"{prompt}{line}")
        else:
            sys.stdout.write(f"\r\n{DIM}…{RESET} {line}")

    # Move cursor to correct visual row and column
    rows_below_cursor = total_rows - rows_above_cursor - 1
    if rows_below_cursor > 0:
        sys.stdout.write(f"\033[{rows_below_cursor}A")
    col = (prefix_len + cursor_col) % width
    sys.stdout.write(f"\r\033[{col}C" if col > 0 else "\r")

    sys.stdout.flush()
    return total_rows


async def read_input() -> str:
    prompt = f"{BLUE}>{RESET} "
    _enter_raw()
    try:
        editor = LineEditor()
        prev_rows = _render(editor, prompt)

        while True:
            data = await _read_bytes()
            key = _parse_key(data)

            match key:
                case "enter":
                    lines = editor.text.split("\n")
                    cursor_line_idx = editor.text[:editor.cursor].count("\n")
                    lines_below = len(lines) - 1 - cursor_line_idx
                    if lines_below > 0:
                        sys.stdout.write(f"\033[{lines_below}B")
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    return editor.text

                case "shift-enter" | "alt-enter":
                    editor.insert("\n")

                case "backspace":
                    editor.backspace()

                case "alt-backspace" | "ctrl-backspace":
                    editor.delete_word_back()

                case "left":
                    editor.move_left()

                case "right":
                    editor.move_right()

                case "ctrl-c":
                    if editor.text:
                        editor = LineEditor()
                        prev_rows = _render(editor, prompt, prev_rows)
                        continue
                    else:
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        raise KeyboardInterrupt

                case "ctrl-d":
                    if not editor.text:
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        raise EOFError

                case s if s.startswith("text:"):
                    editor.insert(s[5:])

                case _:
                    continue

            prev_rows = _render(editor, prompt, prev_rows)

    finally:
        _exit_raw()
