import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".cc"
LAST_SESSION_FILE = CONFIG_DIR / "last_session"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


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
            if obj.get("isCompactSummary"):
                continue
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
