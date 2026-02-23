# cc — minimal Claude Code wrapper

A lightweight Python wrapper around the [Claude Agent SDK](https://github.com/anthropics/claude-code-sdk-python) that gives you Claude Code's full agent capabilities with a clean, SSH-friendly terminal UI.

## Why

Claude Code's built-in TUI is heavy — rich diffs, animated spinners, interactive permission prompts. The Agent SDK exposes the same underlying agent loop (file edits, bash, MCP, session continuity) as a plain Python library. `cc` puts a minimal interface on top.

## Install

```bash
uv tool install .
export ANTHROPIC_API_KEY=sk-ant-...   # add to ~/.zshrc or ~/.bashrc
```

## Usage

```bash
cc                                      # interactive mode
cc "fix the bug in auth.py"            # one-shot
cc -c                                  # continue last session interactively
cc -c "actually, add tests too"        # continue last session with a prompt
cc -r <session_id>                     # resume a specific session
cc -s                                  # pick a session with fzf
cc -p "refactor the payment module"    # plan mode (outputs plan before acting)
cc -y "run tests and fix failures"     # skip all permission prompts
```

```bash
# Pipe-friendly
cat error.log | cc "what's causing this?"
git diff | cc "write a commit message"
```

## Interactive mode

The interactive prompt is a custom input editor — no readline dependency, works cleanly over SSH.

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` / `Alt+Enter` | Insert newline |
| `Alt+←` / `Alt+→` | Move word left/right |
| `Cmd+←` / `Cmd+→` | Jump to line start/end |
| `Alt+Backspace` / `Ctrl+W` | Delete word |
| `Cmd+Backspace` | Delete to line start |
| `c` or `ESC` during response | Cancel streaming, re-edit message |
| `Ctrl+C` | Clear input (or exit if empty) |
| `Ctrl+D` | Exit |

When you cancel a streaming response, the partial output is erased and your original message is restored in the input for editing.

## Config

`~/.cc/config.json`:

```json
{
  "system_prompt": "You are working in a Python monorepo. Prefer uv over pip.",
  "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
  "mcp_servers": {
    "my-server": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    }
  }
}
```

`allowed_tools` defaults to all tools. `mcp_servers` follows Claude Code's MCP config schema.

## Sessions

Every completed response saves a session ID to `~/.cc/last_session`. Use `-c` to continue it or `-r <id>` for a specific one. On resume, the last ~200 lines of context are reprinted. The session ID appears after each response:

```
($0.0031 | 4f2a8b1c)
```
