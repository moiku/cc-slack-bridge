#!/bin/bash
# Claude Code Slack Bridge セットアップスクリプト
# Mac Studio上で実行してください

set -e

BRIDGE_DIR="$HOME/cc-slack-bridge"
mkdir -p "$BRIDGE_DIR"

echo "📦 依存パッケージをインストール中..."
cd "$BRIDGE_DIR"

# uvで仮想環境を作成
uv init --no-workspace 2>/dev/null || true
uv add slack-bolt

echo ""
echo "✅ インストール完了！"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 次の手順："
echo ""
echo "1. .envファイルを設定:"
echo "   cd $BRIDGE_DIR && cp .env.example .env && nano .env"
echo ""
echo "2. ブリッジを起動:"
echo "   cd $BRIDGE_DIR && ./start.sh"
echo ""
echo "3. Slackで使えるコマンド:"
echo "   /cc status          → 全ペイン状況確認"
echo "   /cc p2 '指示内容'   → pane2に指示"
echo "   /cc approve 2       → pane2を承認"
echo "   /cc deny 2          → pane2を拒否"
echo "   /cc log 3           → pane3のログ表示"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
