#!/usr/bin/env bash
# claude-layout.sh
#
# Layout (pane numbers assigned in split order):
#
#  ┌─────────────┬─────────────┬─────────────┐
#  │  pane 1     │  pane 5     │  pane 6     │
#  │  project1   │  extra1     │  extra3     │
#  │  [Orch]     │             │             │
#  ├─────────────┼─────────────┼─────────────┤
#  │  pane 2     │  pane 7     │  pane 8     │
#  │  project2   │  extra2     │  bridge.py  │
#  │  [Worker-A] │             │  (auto)     │
#  ├─────────────┤             │             │
#  │  pane 3     │             │             │
#  │  project3   │             │             │
#  │  [Worker-B] │             │             │
#  ├─────────────┤             │             │
#  │  pane 4     │             │             │
#  │  project4   │             │             │
#  │  [Worker-C] │             │             │
#  └─────────────┴─────────────┴─────────────┘
#
#  Orchestration target : pane 1-4 (left column)
#  pane 1               : Orchestrator (distributes tasks to pane 2-4)
#  pane 5,6,7           : Start Claude Code manually (/cc start <N> also works)
#  pane 8               : bridge.py (auto-started)

SESSION="claude-work"

# ── Directory settings ───────────────────────────────────────────────────────
# Edit each path to point to your actual project directories.
PROJECT1_DIR="$HOME/Projects/project1"   # pane1 Orchestrator
PROJECT2_DIR="$HOME/Projects/project2"   # pane2 Worker-A
PROJECT3_DIR="$HOME/Projects/project3"   # pane3 Worker-B
PROJECT4_DIR="$HOME/Projects/project4"   # pane4 Worker-C
EXTRA1_DIR="$HOME/Projects/extra1"       # pane5 free use
EXTRA2_DIR="$HOME/Projects/extra2"       # pane6 free use
EXTRA3_DIR="$HOME/Projects/extra3"       # pane7 free use
BRIDGE_DIR="$HOME/cc-slack-bridge"       # pane8 bridge.py (do not change)

# ── Session check ────────────────────────────────────────────────────────────
if tmux has-session -t $SESSION 2>/dev/null; then
  tmux attach -t $SESSION
  exit
fi

tmux new-session -d -s $SESSION -n "main"

# ── Build panes ──────────────────────────────────────────────────────────────
# Split order determines pane_index (1-8).
# Left column first (pane 1-4), then centre/right (pane 5-8).

# pane 1: project1 [Orchestrator]
P1=$SESSION:1.1
tmux send-keys -t $P1 "cd \"$PROJECT1_DIR\" && echo '[pane1] project1 (Orchestrator)'" C-m

# pane 2: project2 [Worker-A]  (split down from pane1)
P2=$(tmux split-window -v -t $P1 -P -F '#{pane_id}')
tmux send-keys -t $P2 "cd \"$PROJECT2_DIR\" && echo '[pane2] project2 (Worker-A)'" C-m

# pane 3: project3 [Worker-B]
P3=$(tmux split-window -v -t $P2 -P -F '#{pane_id}')
tmux send-keys -t $P3 "cd \"$PROJECT3_DIR\" && echo '[pane3] project3 (Worker-B)'" C-m

# pane 4: project4 [Worker-C]
P4=$(tmux split-window -v -t $P3 -P -F '#{pane_id}')
tmux send-keys -t $P4 "cd \"$PROJECT4_DIR\" && echo '[pane4] project4 (Worker-C)'" C-m

# pane 5: extra1  (split right from pane1)
P5=$(tmux split-window -h -t $P1 -P -F '#{pane_id}')
tmux send-keys -t $P5 "cd \"$EXTRA1_DIR\" && echo '[pane5] extra1'" C-m

# pane 6: extra3  (split right from pane5)
P6=$(tmux split-window -h -t $P5 -P -F '#{pane_id}')
tmux send-keys -t $P6 "cd \"$EXTRA3_DIR\" && echo '[pane6] extra3'" C-m

# pane 7: extra2  (split down from pane5)
P7=$(tmux split-window -v -t $P5 -P -F '#{pane_id}')
tmux send-keys -t $P7 "cd \"$EXTRA2_DIR\" && echo '[pane7] extra2'" C-m

# pane 8: bridge.py  (split down from pane6, auto-start)
P8=$(tmux split-window -v -t $P6 -P -F '#{pane_id}')
tmux send-keys -t $P8 "cd \"$BRIDGE_DIR\" && echo '[pane8] bridge starting...'" C-m
tmux send-keys -t $P8 "bash start.sh" C-m

# ── Monitoring window ────────────────────────────────────────────────────────
tmux new-window -t $SESSION -n "monitor"
tmux send-keys -t $SESSION:2 "htop" C-m
tmux split-window -v -t $SESSION:2
tmux send-keys -t $SESSION:2 "tail -f ~/.claude/logs/*.log 2>/dev/null || echo 'No logs yet'" C-m

# ── Focus main window pane1 ──────────────────────────────────────────────────
tmux select-window -t $SESSION:1
tmux select-pane -t $SESSION:1.1

echo "Done. Run: tmux attach -t $SESSION"
