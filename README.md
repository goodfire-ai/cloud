# cc — minimal Claude Code wrapper for SSH

A ~180-line Python script that wraps the [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) to give you Claude Code's core functionality without the heavy TUI — useful over slow SSH, in scripts, or if you just prefer a simpler interface.

## Background

Claude Code's CLI is powerful but its terminal UI is expensive to render: rich diffs, animated spinners, interactive permission prompts. All of that lives in the **presentation layer**, which is separate from the actual agent logic.

Under the hood, the Claude Agent SDK (formerly Claude Code SDK) exposes that same agent loop — file reading/writing, bash execution, MCP servers, session management, hooks — as a Python/TypeScript library. The SDK literally bundles and spawns the Claude Code CLI as a subprocess, so you get identical capability with a UI you control.

`cc` is a thin, opinionated wrapper around the SDK's `ClaudeSDKClient`.

## Installation

```bash
# With uv (recommended)
uv tool install .

# Or manually
pip install claude-agent-sdk
chmod +x cc.py
ln -s $(realpath cc.py) ~/.local/bin/cc
```

You'll also need an API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...  # add to ~/.bashrc or ~/.zshrc
```

## Usage

```bash
cc "fix the bug in auth.py"               # one-shot prompt
cc                                         # reads prompt from stdin (good for piping)
cc -c "actually, also add tests"           # continue last session
cc -r abc123 "go back to this session"    # resume by session ID
cc -p "refactor the payment module"        # plan mode: outputs plan before acting
cc -y "run the test suite and fix failures" # skip all permission prompts
cc -y -p "scaffold a new FastAPI service"  # combine flags
```

```bash
# Works well with pipes
cat error.log | cc "what's causing this?"
git diff | cc "write a commit message for this"
```

## Config

Create `~/.cc/config.json` to set persistent options:

```json
{
  "system_prompt": "You are working in a Python monorepo. Prefer uv over pip.",
  "mcp_servers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_..." }
    }
  },
  "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
}
```

`allowed_tools` defaults to all tools if omitted. `mcp_servers` follows the same schema as Claude Code's own MCP config.

## Sessions

Every completed query saves a session ID to `~/.cc/last_session`. Use `-c` to continue that session (same conversation context), or `-r <id>` to resume any previous session by ID.

The session ID is printed in dim text after each response:
```
($0.0031 | 4f2a8b1c)
```

## Plan mode (`-p`)

Injects a system prompt instructing Claude to output a numbered plan before taking any action. Not identical to the CLI's built-in plan mode (which has a distinct UI state and requires explicit approval), but achieves the same intent: you see what Claude intends to do before it does it.

## What's not here

Things the CLI has that this doesn't bother with:

- **Diff rendering** — file edits show as `[Edit] path/to/file`, not a visual diff
- **Interactive permission prompts** — use `-y` to skip, or implement hooks in the source for custom logic
- **Slash commands** — use `-c` for continuation; anything else just put in your prompt
- **IDE integration** — this is terminal-only by design
- **Cost tracking beyond per-session** — the SDK returns `total_cost_usd` per `ResultMessage`; extend if you want a running total

## Caveats

- The SDK spawns the Claude Code CLI as a subprocess internally, so Claude Code still needs to be installed (the SDK pip package bundles it, so `pip install claude-agent-sdk` should handle this).
