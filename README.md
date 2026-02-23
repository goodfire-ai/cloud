# cloud — minimal Claude Code wrapper

`cloud` is Claude Code with a lighter terminal UI — same agent, same tools, same MCP servers, same sessions. Just a frontend that works well over SSH.

## Install

```bash
uv tool install git+https://github.com/goodfire-ai/cloud
```

Then add your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # add to ~/.zshrc or ~/.bashrc
```

## Usage

```bash
cloud                                          # interactive mode
cloud "fix the bug in auth.py"                # one-shot
cloud -c                                       # continue last session
cloud -c "actually, add tests too"            # continue with a prompt
cloud -r <session_id>                          # resume a specific session
cloud -m opus "refactor the payment module"   # use a specific model
cloud --dangerously-skip-permissions "..."    # bypass permission prompts
```

```bash
# Pipe-friendly
cat error.log | cloud "what's causing this?"
git diff | cloud "write a commit message"
```

## Why

Claude Code's built-in TUI doesn't play well over SSH — heavy rendering, lots of escape sequences, interactive permission prompts. `cloud` replaces the frontend while keeping everything else identical: the same Claude Code agent runs under the hood, reading your `~/.claude/settings.json`, honouring your MCP servers and tool permissions, and sharing the same session history.

## Interactive mode

Custom input editor — no readline dependency, works cleanly over SSH.

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

MCP servers, allowed tools, model defaults — all inherited automatically from Claude Code's own config at `~/.claude/settings.json`. No separate config needed.

## Sessions

Every response saves a session ID to `~/.cc/last_session`. Use `-c` to continue it or `-r <id>` for a specific one. On resume, the last ~200 lines of context are reprinted. The session ID appears after each response:

```
($0.0031 | 4f2a8b1c)
```
