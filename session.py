import json
import subprocess
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".cc"
CONFIG_FILE = CONFIG_DIR / "config.json"
LAST_SESSION_FILE = CONFIG_DIR / "last_session"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


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


def find_session_file(session_id: str) -> Path | None:
    for f in CLAUDE_PROJECTS_DIR.rglob(f"{session_id}.jsonl"):
        return f
    return None


def load_session_messages(session_id: str) -> list[tuple[str, list]]:
    """Return all (role, content_blocks) pairs from a session."""
    f = find_session_file(session_id)
    if not f:
        return []

    exchanges: list[tuple[str, list]] = []
    with open(f) as fh:
        for line in fh:
            obj = json.loads(line)
            t = obj.get("type")
            msg = obj.get("message", {})
            role = msg.get("role") if isinstance(msg, dict) else None
            if t not in ("user", "assistant") or role not in ("user", "assistant"):
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if content:
                exchanges.append((role, content))
    return exchanges


def pick_session_fzf() -> str:
    """Interactive fzf picker over all Claude Code sessions. Returns session ID."""
    assert CLAUDE_PROJECTS_DIR.is_dir(), f"No sessions found at {CLAUDE_PROJECTS_DIR}"

    sessions: list[tuple[float, str, str, str]] = []  # (mtime, sid, project, summary)
    home_slug = str(Path.home()).replace("/", "-")
    for proj_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            sid = f.stem
            mtime = f.stat().st_mtime
            summary = None
            with open(f) as fh:
                for line in fh:
                    obj = json.loads(line)
                    if obj.get("type") == "summary":
                        summary = obj["summary"]
                        break
            if not summary:
                continue
            proj_name = proj_dir.name[len(home_slug):].lstrip("-") or proj_dir.name
            sessions.append((mtime, sid, proj_name, summary))

    assert sessions, "No sessions with summaries found"
    sessions.sort(reverse=True)

    fzf_lines = [
        f"{sid}\t{datetime.fromtimestamp(mtime).strftime('%m/%d %H:%M')}  {proj:20s}  {summary}"
        for mtime, sid, proj, summary in sessions
    ]

    result = subprocess.run(
        ["fzf", "--with-nth=2..", "--delimiter=\t", "--no-sort", "--ansi"],
        input="\n".join(fzf_lines),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "No session selected"
    return result.stdout.strip().split("\t")[0]
