#!/bin/bash
# cc-slack-bridge 起動スクリプト

BRIDGE_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$BRIDGE_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env ファイルが見つかりません"
    echo "   cp $BRIDGE_DIR/.env.example $BRIDGE_DIR/.env"
    echo "   して設定してください"
    exit 1
fi

# .env を読み込む
set -a
source "$ENV_FILE"
set +a

echo "🚀 Claude Code Slack Bridge 起動中..."
echo "   セッション: $TMUX_SESSION"
echo "   ペイン数:   $NUM_PANES"
echo "   チャンネル: $SLACK_CHANNEL"
echo ""

cd "$BRIDGE_DIR"
uv run python bridge.py
