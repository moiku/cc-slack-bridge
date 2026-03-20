# cc-slack-bridge

Control and monitor [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions running in tmux — remotely, from Slack.

## What it does

- **Approval notifications** — When Claude Code asks for permission, a Slack message with buttons appears instantly. Tap to approve or deny from your phone.
- **Remote commands** — Start, stop, restart Claude Code in any pane via `/cc` slash commands.
- **Shell execution** — Run shell commands in any pane and get the output back in Slack.
- **File transfer** — Upload files to your Mac by attaching them in Slack. Download files from any pane back to Slack.
- **Orchestration** — Send a single instruction to the Orchestrator pane (pane1); it distributes subtasks to Worker panes (pane2–4) via shared task files.
- **Auto-update detection** — Checks for new Claude Code versions hourly via `brew`. One button triggers stop → upgrade → restart.

## Layout

```
┌─────────────┬─────────────┬─────────────┐
│  pane 1     │  pane 5     │  pane 6     │
│  project1   │  extra1     │  extra3     │
│  [Orch]     │             │             │
├─────────────┼─────────────┼─────────────┤
│  pane 2     │  pane 7     │  pane 8     │
│  project2   │  extra2     │  bridge.py  │
│  [Worker-A] │             │  (auto)     │
├─────────────┤             │             │
│  pane 3     │             │             │
│  project3   │             │             │
│  [Worker-B] │             │             │
├─────────────┤             │             │
│  pane 4     │             │             │
│  project4   │             │             │
│  [Worker-C] │             │             │
└─────────────┴─────────────┴─────────────┘
```

- **pane 1–4** — Claude Code orchestration targets (`/cc start all` covers these)
- **pane 5–7** — Free use; start Claude Code manually or via `/cc start <N>`
- **pane 8** — `bridge.py` auto-starts here; excluded from all commands

## Requirements

- macOS with Apple Silicon (tested on Mac Studio)
- [tmux](https://github.com/tmux/tmux) `brew install tmux`
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed via Homebrew
- [uv](https://github.com/astral-sh/uv) `brew install uv`
- A Slack workspace where you can create apps

## Setup

### 1. Create a Slack App

1. Go to https://api.slack.com/apps → **Create New App** → From scratch
2. **Settings → Socket Mode** → Enable → generate an App-Level Token with scope `connections:write` → copy `xapp-...`
3. **Features → OAuth & Permissions** → add Bot Token Scopes:
   ```
   chat:write
   commands
   app_mentions:read
   files:write
   files:read
   ```
4. **Install to Workspace** → copy `xoxb-...`
5. **Features → Slash Commands** → Create `/cc` (no Request URL needed with Socket Mode)
6. Invite the bot to your notification channel: `/invite @your-bot-name`

> **DM notifications**: set `SLACK_CHANNEL` to your own Member ID (`U0123ABCDEF`) to receive notifications as DMs instead of channel messages. Find your Member ID in Slack: click your name → Profile → ⋯ → Copy member ID.

### 2. Install

```bash
git clone https://github.com/yourname/cc-slack-bridge.git ~/cc-slack-bridge
cd ~/cc-slack-bridge
cp .env.example .env
```

Edit `.env`:

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL=U0123ABCDEF        # your Member ID for DM, or #channel-name
ALLOWED_SLACK_USER_ID=U0123ABCDEF  # restrict to yourself (recommended)
TMUX_SESSION=claude-work
CLAUDE_CMD=claude
TASKS_DIR=/Users/yourname/cc-tasks
UPLOAD_DIR=/Users/yourname/Projects/upload
```

Install dependencies:

```bash
uv init --no-workspace
uv add slack-bolt
```

### 3. Configure the layout

Edit `claude-layout.sh` — set each directory to your actual project paths:

```bash
PROJECT1_DIR="$HOME/Projects/my-research"
PROJECT2_DIR="$HOME/Projects/paper-draft"
PROJECT3_DIR="$HOME/Projects/experiments"
PROJECT4_DIR="$HOME/Projects/tools"
EXTRA1_DIR="$HOME/Projects/sandbox"
EXTRA2_DIR="$HOME/Documents"
EXTRA3_DIR="$HOME/Projects/misc"
```

Install the layout script:

```bash
mkdir -p ~/.tmux
cp claude-layout.sh ~/.tmux/claude-layout.sh
chmod +x ~/.tmux/claude-layout.sh
```

### 4. Start

```bash
bash ~/.tmux/claude-layout.sh
tmux attach -t claude-work
```

`bridge.py` starts automatically in pane 8. Check the pane for `[bridge] Slack Socket Mode 起動中...` to confirm it's connected.

## Slash commands

| Command | Description |
|---------|-------------|
| `/cc status` | Show all pane states, current directories, approval warnings |
| `/cc start all` | Start Claude Code in pane 1–4 |
| `/cc start <N>` | Start Claude Code in pane N (1–7) |
| `/cc stop <N\|all>` | Stop Claude Code |
| `/cc restart <N\|all>` | Restart Claude Code |
| `/cc sh <N> <cmd>` | Run a shell command in pane N, get output in Slack |
| `/cc get <N> <file>` | Send a file from pane N's working directory to Slack |
| `/cc orch <instruction>` | Send instruction to Orchestrator (pane1); subtasks distributed to Workers |
| `/cc p<N> <text>` | Send text directly to pane N |
| `/cc approve <N>` | Send `y` to pane N |
| `/cc deny <N>` | Send `n` to pane N |
| `/cc log <N>` | Show last 100 lines from pane N |
| `/cc version` | Check installed vs latest Claude Code version |
| `/cc update` | Stop all → `brew upgrade claude-code` → restart all |

### File upload

Attach any file to a Slack message in your notification channel:
- No pane mentioned → saved to `UPLOAD_DIR` (`~/Projects/upload` by default)
- Include `pane2` in the message → saved to pane 2's current working directory

### Approval flow

When Claude Code presents a numbered menu:

```
 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, allow reading from this project
   3. No
```

bridge.py detects the prompt and posts a Slack message with one button per choice. Tap to respond without touching the terminal.

## Orchestration

```
/cc orch "write tests for all projects"
          ↓
     pane1 (Orchestrator)
     Claude Code splits the task and writes:
          ↓
  ~/cc-tasks/pane2.md   pane3.md   pane4.md
          ↓  (bridge.py delivers every 5 seconds)
     pane2        pane3        pane4
   Worker-A     Worker-B     Worker-C
          ↓
  ~/cc-tasks/results/pane*.md
```

## Security

- Set `ALLOWED_SLACK_USER_ID` to your own Member ID to block all other users
- Shell commands via `/cc sh` block destructive patterns: `rm -rf`, `dd`, `mkfs`, `shutdown`, `reboot`, etc.
- The Slack App uses Internal distribution only — not visible outside your workspace

## File structure

```
cc-slack-bridge/
├── bridge.py          # Main bridge process
├── start.sh           # Start script (loads .env, runs bridge.py)
├── setup.sh           # Dependency installer
├── .env.example       # Environment variable template
└── claude-layout.sh   # tmux layout script (copy to ~/.tmux/)
```

## License

MIT
