#!/usr/bin/env python3
"""
Claude Code Slack Bridge v4
レイアウト: claude-work セッション、mainウィンドウ(1)
  左列  pane1=Projects[Orch] / pane2,3,4=Projects[Worker]
  中列  pane5=外部資金応募   / pane7=sandbox
  右列  pane6=講演依頼       / pane8=bridge自身（監視除外）

追加機能:
  - /cc sh <N> <cmd>  : ペインでシェルコマンド実行・結果返信
  - /cc get <N> <file>: ペインのカレントディレクトリからSlackへファイル送信
  - ファイル添付イベント: Slackに添付 → Mac Studioに転送
"""

import os, re, time, subprocess, threading, tempfile, json
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import urllib.request

# ── 認証 ────────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
NOTIFY_CHANNEL  = os.environ.get("SLACK_CHANNEL", "#claude-code")
ALLOWED_USER_ID = os.environ.get("ALLOWED_SLACK_USER_ID", "")

# ── tmux セッション ──────────────────────────────────────────────────────────
TMUX_SESSION = os.environ.get("TMUX_SESSION", "claude-work")
TMUX_WINDOW  = "1"

# ── ペイン構成 ───────────────────────────────────────────────────────────────
ORCHESTRATOR_PANE = 1
WORKER_PANES      = [2, 3, 4]
MONITOR_PANES     = [1, 2, 3, 4, 5, 6, 7]
CC_PANES          = [1, 2, 3, 4]

PANE_LABELS = {
    1: "project1 [Orch]",
    2: "project2 [Worker-A]",
    3: "project3 [Worker-B]",
    4: "project4 [Worker-C]",
    5: "extra1",
    6: "extra2",
    7: "extra3",
    8: "bridge (self)",
}

# ── ディレクトリ設定 ─────────────────────────────────────────────────────────
CLAUDE_CMD   = os.environ.get("CLAUDE_CMD", "claude")
TASKS_DIR    = Path(os.environ.get("TASKS_DIR",   str(Path.home() / "cc-tasks")))
UPLOAD_DIR   = Path(os.environ.get("UPLOAD_DIR",  str(Path.home() / "Projects" / "upload")))

TASKS_DIR.mkdir(parents=True, exist_ok=True)
(TASKS_DIR / "results").mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── シェル実行タイムアウト ────────────────────────────────────────────────────
SH_TIMEOUT = int(os.environ.get("SH_TIMEOUT", "30"))  # 秒

# ── 承認検出 ─────────────────────────────────────────────────────────────────
APPROVAL_RE = re.compile(
    r"Do you want to proceed|Allow this action|\(y/n\)|\[Y/n\]|\[y/N\]"
    r"|Press Enter to confirm|Approve\?|続行しますか|許可しますか"
    r"|❯\s*1\.|>\s*1\.",   # Claude Code の番号選択メニュー
    re.IGNORECASE
)

POLL_INTERVAL         = float(os.environ.get("POLL_INTERVAL", "5"))
UPDATE_CHECK_INTERVAL = 3600

# ── グローバル状態 ───────────────────────────────────────────────────────────
pane_last_output: dict[int, str] = {}
pending_approvals: set[int]      = set()
current_version: str             = ""
# pane番号 → tmux pane_id (%N形式) のマップ（起動時に構築）
pane_id_map: dict[int, str] = {}

app = App(token=SLACK_BOT_TOKEN)

# ── 基本ユーティリティ ───────────────────────────────────────────────────────

def auth_ok(obj) -> bool:
    if not ALLOWED_USER_ID:
        return True
    uid = obj.get("user_id") or obj.get("user") or ""
    return uid == ALLOWED_USER_ID

def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_pane_id_map() -> dict[int, str]:
    """pane番号 → pane_id (%N) のマップを構築"""
    r = subprocess.run(
        ["tmux", "list-panes", "-t", f"{TMUX_SESSION}:{TMUX_WINDOW}",
         "-F", "#{pane_index} #{pane_id}"],
        capture_output=True, text=True)
    m = {}
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            m[int(parts[0])] = parts[1]
    return m

def pane_target(idx: int) -> str:
    """pane_id_mapがあればpane_id(%N)で指定、なければ従来のwindow.index形式"""
    pid = pane_id_map.get(idx)
    if pid:
        return pid
    return f"{TMUX_SESSION}:{TMUX_WINDOW}.{idx}"

def tmux_capture(idx: int, lines: int = 50) -> str:
    r = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", pane_target(idx), f"-S-{lines}"],
        capture_output=True, text=True)
    return r.stdout.strip()

def tmux_send(idx: int, text: str, enter: bool = True) -> bool:
    """テキストを送信し、enter=Trueなら別途Enterキーを送る"""
    # テキストを送信（末尾の改行は含めない）
    if text:
        r = subprocess.run(
            ["tmux", "send-keys", "-t", pane_target(idx), text, ""],
            capture_output=True)
        if r.returncode != 0:
            return False
    # Enterキーを別コマンドで送信（Claude Codeのプロンプトでも確実に動く）
    if enter:
        subprocess.run(
            ["tmux", "send-keys", "-t", pane_target(idx), "Enter"],
            capture_output=True)
    return True

def pane_exists(idx: int) -> bool:
    r = subprocess.run(
        ["tmux", "list-panes", "-t", f"{TMUX_SESSION}:{TMUX_WINDOW}", "-F", "#{pane_index}"],
        capture_output=True, text=True)
    return str(idx) in r.stdout.split()

def get_pane_cwd(idx: int) -> str:
    """ペインのカレントディレクトリを取得"""
    r = subprocess.run(
        ["tmux", "display-message", "-p", "-t", pane_target(idx), "#{pane_current_path}"],
        capture_output=True, text=True)
    return r.stdout.strip() or str(Path.home())

# ── シェルコマンド実行 ────────────────────────────────────────────────────────

# 危険コマンドdenylist
_DENY_RE = re.compile(
    r"""^\s*("""
    r"""rm\s+(-[a-z]*[fr][a-z]*\s+|--force\s+|--recursive\s+)"""  # rm -rf/-f/-r
    r"""|rmdir"""
    r"""|dd"""
    r"""|mkfs"""
    r"""|fdisk"""
    r"""|shutdown|reboot|halt|poweroff"""
    r"""|killall"""
    r"""|chmod\s+-[Rr]\s+777"""
    r"""|>\s*/dev/sd"""
    r"""|mv\s+.+\s+/dev/null"""
    r""")""",
    re.IGNORECASE
)

def run_shell_in_pane(idx: int, cmd: str) -> str:
    """
    ペインのカレントディレクトリでコマンドを実行して結果を返す。
    cdは状態を変えるので tmux send-keys で処理。denylistで危険コマンドをブロック。
    """
    # denylistチェック
    if _DENY_RE.search(cmd):
        return f"🚫 ブロック: `{cmd}`\n危険なコマンドは実行できません"

    cwd = get_pane_cwd(idx)

    # cd は状態変更なのでペインに直接送る
    if re.match(r"^\s*cd\b", cmd):
        tmux_send(idx, cmd)
        time.sleep(0.3)
        new_cwd = get_pane_cwd(idx)
        return f"📁 カレントディレクトリ変更:\n`{cwd}` → `{new_cwd}`"

    # それ以外はsubprocessで実行して出力を取得
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True,
            timeout=SH_TIMEOUT
        )
        out = (r.stdout + r.stderr).strip()
        if not out:
            out = "(出力なし)"
        # 長すぎる場合は末尾を切る
        if len(out) > 2000:
            out = "...(省略)...\n" + out[-1800:]
        status = "✅" if r.returncode == 0 else f"❌ (exit {r.returncode})"
        return f"{status} `{cmd}` @ `{cwd}`\n```\n{out}\n```"
    except subprocess.TimeoutExpired:
        return f"⏱ タイムアウト ({SH_TIMEOUT}秒) : `{cmd}`"
    except Exception as e:
        return f"❌ エラー: {e}"

# ── ファイルダウンロード（Slackへ送信）───────────────────────────────────────

def send_file_to_slack(idx: int, filepath_str: str, channel: str) -> str:
    """ペインのcwdを基準にファイルを解決してSlackにアップロード"""
    p = Path(filepath_str)
    if not p.is_absolute():
        cwd = get_pane_cwd(idx)
        p = Path(cwd) / p
    p = p.expanduser().resolve()

    if not p.exists():
        return f"❌ ファイルが見つかりません: `{p}`"
    if not p.is_file():
        return f"❌ ファイルではありません: `{p}`"

    size_mb = p.stat().st_size / 1_048_576
    label   = PANE_LABELS.get(idx, f"pane{idx}")

    try:
        result = app.client.files_upload_v2(
            channel=channel,
            file=str(p),
            filename=p.name,
            title=f"[pane{idx}:{label}] {p.name}",
            initial_comment=f"📎 pane{idx} ({label}) の `{p}` ({size_mb:.2f} MB)"
        )
        if result["ok"]:
            return f"✅ `{p.name}` を送信しました ({size_mb:.2f} MB)"
        return f"❌ アップロード失敗: {result.get('error','unknown')}"
    except Exception as e:
        return f"❌ アップロードエラー: {e}"

# ── ファイルアップロード（Slackから受信）─────────────────────────────────────

def download_slack_file(url: str, dest: Path) -> bool:
    """Slack のファイルURLからダウンロード"""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"})
    try:
        with urllib.request.urlopen(req) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"[bridge] download error: {e}")
        return False

def handle_file_share(event: dict) -> None:
    """
    Slackへのファイル添付を処理。
    メッセージ本文に "pane<N>" があればそのペインのcwdに保存。
    なければ UPLOAD_DIR に保存。
    """
    text     = event.get("text", "") or ""
    channel  = event.get("channel", NOTIFY_CHANNEL)
    files    = event.get("files", [])

    # メッセージからペイン番号を抽出
    m         = re.search(r"pane\s*(\d+)", text, re.IGNORECASE)
    pane_idx  = int(m.group(1)) if m else None

    for f in files:
        url      = f.get("url_private_download") or f.get("url_private")
        filename = f.get("name", "uploaded_file")
        if not url:
            continue

        if pane_idx and pane_exists(pane_idx):
            dest_dir = Path(get_pane_cwd(pane_idx))
            label    = f"pane{pane_idx} ({PANE_LABELS.get(pane_idx,'')}) のcwd"
        else:
            dest_dir = UPLOAD_DIR
            label    = f"デフォルト ({UPLOAD_DIR})"

        dest = dest_dir / filename
        # 同名ファイルがあれば連番付与
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            for i in range(1, 100):
                dest = dest_dir / f"{stem}_{i}{suffix}"
                if not dest.exists():
                    break

        if download_slack_file(url, dest):
            app.client.chat_postMessage(
                channel=channel,
                text=f"📥 `{filename}` を保存しました\n保存先: `{dest}`\n宛先: {label}"
            )
        else:
            app.client.chat_postMessage(
                channel=channel,
                text=f"❌ `{filename}` のダウンロードに失敗しました"
            )

# ── バージョン管理 ───────────────────────────────────────────────────────────

def get_installed_ver() -> str:
    r = subprocess.run(["brew", "list", "--versions", "claude-code"], capture_output=True, text=True)
    m = re.search(r"claude-code\s+([\d.]+)", r.stdout)
    return m.group(1) if m else "unknown"

def get_latest_ver() -> str:
    r = subprocess.run(["brew", "info", "--json=v2", "claude-code"], capture_output=True, text=True)
    try:
        info = json.loads(r.stdout)
        return info["formulae"][0]["versions"]["stable"]
    except Exception:
        return ""

def do_brew_update() -> str:
    subprocess.run(["brew", "update"], capture_output=True, timeout=60)
    r = subprocess.run(["brew", "upgrade", "claude-code"], capture_output=True, text=True, timeout=180)
    ver = get_installed_ver()
    if r.returncode == 0:
        return f"✅ アップデート完了: {ver}"
    if "already installed" in r.stdout + r.stderr:
        return f"✅ すでに最新版です: {ver}"
    return f"❌ アップデート失敗:\n```{esc(r.stderr[-400:])}```"

# ── Claude Code プロセス管理 ─────────────────────────────────────────────────

def start_cc(idx: int) -> None:
    tmux_send(idx, CLAUDE_CMD)

def stop_cc(idx: int) -> None:
    subprocess.run(["tmux", "send-keys", "-t", pane_target(idx), "C-c", ""], capture_output=True)
    time.sleep(0.5)
    tmux_send(idx, "/exit")

def restart_cc(idx: int) -> None:
    stop_cc(idx); time.sleep(1); start_cc(idx)

def stop_all() -> list[int]:
    done = [i for i in CC_PANES if pane_exists(i)]
    for i in done: stop_cc(i)
    return done

def start_all() -> list[int]:
    done = []
    for i in CC_PANES:
        if pane_exists(i):
            time.sleep(0.3); start_cc(i); done.append(i)
    return done

# ── オーケストレーション ─────────────────────────────────────────────────────

def orchestrate(instruction: str) -> str:
    for i in WORKER_PANES:
        (TASKS_DIR / f"pane{i}.md").unlink(missing_ok=True)
    prompt = (
        f"{instruction}\n\n"
        f"（各ワーカーへのサブタスクを {TASKS_DIR}/pane<N>.md に書き出してください。"
        f"完了結果は {TASKS_DIR}/results/pane<N>.md に保存してください）"
    )
    tmux_send(ORCHESTRATOR_PANE, prompt)
    # Claude Codeがプロンプト入力待ちの場合に備えて確実にEnterを送る
    time.sleep(0.3)
    tmux_send(ORCHESTRATOR_PANE, "", enter=True)
    return f"📋 オーケストレーター (pane{ORCHESTRATOR_PANE}) に送信:\n> {instruction}"

def deliver_tasks() -> None:
    for i in WORKER_PANES:
        f = TASKS_DIR / f"pane{i}.md"
        if f.exists():
            content = f.read_text(); f.unlink()
            tmux_send(i, content)

# ── Slack 通知 ───────────────────────────────────────────────────────────────

def notify(text: str, blocks=None) -> None:
    app.client.chat_postMessage(channel=NOTIFY_CHANNEL, text=text, blocks=blocks)

def parse_approval_choices(snippet: str) -> list[tuple[str, str]]:
    """
    Claude Codeの承認メニューから選択肢を抽出して返す。
    選択肢の特徴:
      - "❯ 1. ..." または "  1. ..." の形式
      - カーソル(❯)がある行 or その直後の連続した番号行
      - 2行以上連続している場合のみ有効（説明文の箇条書きと区別）
    """
    lines = snippet.splitlines()
    # まずカーソル付き行(❯)を探す
    cursor_re = re.compile(r"^\s*❯\s*(\d+)\.\s*(.+)")
    item_re   = re.compile(r"^\s*(\d+)\.\s*(.+)")

    # カーソル行を起点にブロックを探す
    for i, line in enumerate(lines):
        m = cursor_re.match(line)
        if not m:
            continue
        # カーソル行から前後の連続する番号行をすべて収集
        choices = [(m.group(1), m.group(2).strip())]
        # 後続行を走査
        for j in range(i + 1, min(i + 10, len(lines))):
            nxt = item_re.match(lines[j])
            if nxt:
                choices.append((nxt.group(1), nxt.group(2).strip()))
            elif lines[j].strip() == "":
                continue
            else:
                break
        # 前方行も走査（カーソルより前の選択肢）
        pre = []
        for j in range(i - 1, max(i - 10, -1), -1):
            prv = item_re.match(lines[j])
            if prv:
                pre.insert(0, (prv.group(1), prv.group(2).strip()))
            elif lines[j].strip() == "":
                continue
            else:
                break
        choices = pre + choices
        # 番号が連続しているか検証（1,2,3...）
        nums = [int(c[0]) for c in choices]
        if nums == list(range(1, len(nums) + 1)) and len(nums) >= 2:
            return choices

    return []

def notify_approval(idx: int, snippet: str) -> None:
    label   = PANE_LABELS.get(idx, f"pane{idx}")
    choices = parse_approval_choices(snippet)

    # 選択肢ボタンを動的生成（最大5個、Slack上限）
    if choices:
        elements = []
        for num, text in choices[:5]:
            # 最後の選択肢（No系）は danger、それ以外は primary or default
            is_no = re.search(r"\bno\b|cancel|拒否|キャンセル", text, re.IGNORECASE)
            is_yes = num == "1"
            btn = {
                "type": "button",
                "text": {"type": "plain_text", "text": f"{num}. {text[:30]}"},
                "action_id": f"choice_{idx}_{num}",
                "value": f"{idx}:{num}",
            }
            if is_yes:
                btn["style"] = "primary"
            elif is_no:
                btn["style"] = "danger"
            elements.append(btn)
    else:
        # 従来の y/n ボタン
        elements = [
            {"type": "button", "text": {"type": "plain_text", "text": "✅ 承認 (y)"},
             "style": "primary", "action_id": f"approve_{idx}", "value": str(idx)},
            {"type": "button", "text": {"type": "plain_text", "text": "❌ 拒否 (n)"},
             "style": "danger", "action_id": f"deny_{idx}", "value": str(idx)},
        ]

    notify(f"⚠️ Pane {idx} ({label}) が承認を求めています", blocks=[
        {"type": "header", "text": {"type": "plain_text",
         "text": f"⚠️ Pane {idx}: {label} 承認リクエスト"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"```{esc(snippet[-400:])}```"}},
        {"type": "actions", "block_id": f"approval_{idx}", "elements": elements},
    ])

def notify_update_available(installed: str, latest: str) -> None:
    notify(f"🆕 Claude Code アップデートあり: {installed} → {latest}", blocks=[
        {"type": "header", "text": {"type": "plain_text", "text": "🆕 Claude Code アップデート検出"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"インストール済: `{installed}`\n最新版: `{latest}`"}},
        {"type": "actions", "block_id": "update_action", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "🔄 全停止→brew upgrade→全再起動"},
             "style": "primary", "action_id": "do_full_update", "value": "update"},
            {"type": "button", "text": {"type": "plain_text", "text": "⏭ スキップ"},
             "action_id": "skip_update", "value": "skip"},
        ]},
    ])

# ── ポーリング ───────────────────────────────────────────────────────────────

def polling_loop():
    global current_version, pane_id_map
    current_version = get_installed_ver()
    last_ver_check  = time.time()
    pane_id_map     = build_pane_id_map()
    print(f"[bridge] 起動 | session={TMUX_SESSION} | CC={current_version}")
    print(f"[bridge] pane_id_map={pane_id_map}")

    while True:
        for i in MONITOR_PANES:
            try:
                out = tmux_capture(i)
                if out != pane_last_output.get(i):
                    pane_last_output[i] = out
                    if APPROVAL_RE.search(out) and i not in pending_approvals:
                        pending_approvals.add(i); notify_approval(i, out)
                    elif not APPROVAL_RE.search(out):
                        pending_approvals.discard(i)
            except Exception as e:
                print(f"[bridge] pane{i} error: {e}")

        deliver_tasks()

        if time.time() - last_ver_check > UPDATE_CHECK_INTERVAL:
            last_ver_check = time.time()
            try:
                latest = get_latest_ver()
                if latest and latest != current_version:
                    notify_update_available(current_version, latest)
            except Exception as e:
                print(f"[bridge] version check error: {e}")

        time.sleep(POLL_INTERVAL)

# ── ファイル添付イベント ──────────────────────────────────────────────────────

@app.event("message")
def handle_message_events(event, say):
    """ファイル添付を含むメッセージを処理"""
    # 自分自身のBot投稿は無視
    if event.get("bot_id"):
        return
    # ALLOWED_USER_ID チェック
    if ALLOWED_USER_ID and event.get("user") != ALLOWED_USER_ID:
        return
    # ファイルが含まれていれば処理
    if event.get("files"):
        threading.Thread(target=handle_file_share, args=(event,), daemon=True).start()

# ── /cc コマンド ─────────────────────────────────────────────────────────────

@app.command("/cc")
def handle_cc(ack, respond, command):
    ack()
    if not auth_ok(command):
        respond("🚫 このBotはプライベートです"); return

    args = command["text"].strip().split(maxsplit=1)
    sub  = args[0].lower() if args else ""

    # ── status ──────────────────────────────────────────────────────────────
    if sub == "status":
        ver    = get_installed_ver()
        blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"📊 Claude Code v{ver}"}}]
        for i in MONITOR_PANES:
            out   = pane_last_output.get(i, "(データなし)")
            label = PANE_LABELS.get(i, f"pane{i}")
            icon  = "🔴" if i in pending_approvals else ("🟣" if i == ORCHESTRATOR_PANE else "🟢")
            warn  = " ─ 承認待ち！" if i in pending_approvals else ""
            cwd   = get_pane_cwd(i)
            blocks += [
                {"type": "section", "text": {"type": "mrkdwn",
                 "text": f"{icon} *Pane {i}* {label}{warn}\n`📁 {cwd}`\n```{esc(out[-180:])}```"}},
                {"type": "divider"},
            ]
        respond(blocks=blocks)

    # ── start / stop / restart ───────────────────────────────────────────────
    elif sub in ("start", "stop", "restart"):
        target = args[1].strip() if len(args) > 1 else ""
        if target == "all":
            idxs = CC_PANES[:]
        elif target.isdigit() and int(target) in MONITOR_PANES:
            idxs = [int(target)]
        else:
            respond(f"❌ `/cc {sub} <1-7|all>`  ※ all は pane1〜4 のみ"); return
        results = []
        for i in idxs:
            if not pane_exists(i):
                results.append(f"pane{i}: ペインが見つかりません"); continue
            if sub == "start":
                start_cc(i);   results.append(f"pane{i} ({PANE_LABELS.get(i,'')}): ▶️ 起動")
            elif sub == "stop":
                stop_cc(i);    results.append(f"pane{i} ({PANE_LABELS.get(i,'')}): ⏹ 停止")
            else:
                restart_cc(i); results.append(f"pane{i} ({PANE_LABELS.get(i,'')}): 🔄 再起動")
            time.sleep(0.4)
        respond("\n".join(results))

    # ── sh <N> <コマンド> ────────────────────────────────────────────────────
    elif sub == "sh" and len(args) == 2:
        sh_args = args[1].split(maxsplit=1)
        if len(sh_args) < 2 or not sh_args[0].isdigit():
            respond("❌ `/cc sh <N> <コマンド>`  例: `/cc sh 2 ls -la`"); return
        idx = int(sh_args[0])
        if idx not in MONITOR_PANES:
            respond(f"❌ pane{idx} は対象外です（有効: {MONITOR_PANES}）"); return
        cmd = sh_args[1]
        label = PANE_LABELS.get(idx, f"pane{idx}")
        respond(f"⚙️ pane{idx} ({label}) で実行中: `{cmd}`")
        def _sh():
            result = run_shell_in_pane(idx, cmd)
            notify(result)
        threading.Thread(target=_sh, daemon=True).start()

    # ── get <N> <ファイル> ───────────────────────────────────────────────────
    elif sub == "get" and len(args) == 2:
        get_args = args[1].split(maxsplit=1)
        if len(get_args) < 2 or not get_args[0].isdigit():
            respond("❌ `/cc get <N> <ファイル名>`  例: `/cc get 3 results.csv`"); return
        idx      = int(get_args[0])
        filepath = get_args[1]
        label    = PANE_LABELS.get(idx, f"pane{idx}")
        respond(f"📤 pane{idx} ({label}) の `{filepath}` を送信します...")
        def _get():
            result = send_file_to_slack(idx, filepath, command["channel_id"])
            notify(result)
        threading.Thread(target=_get, daemon=True).start()

    # ── update ───────────────────────────────────────────────────────────────
    elif sub == "update":
        respond("🔄 全停止 → brew upgrade claude-code → 全再起動 を開始します...")
        def _upd():
            global current_version
            stop_all(); time.sleep(2)
            msg = do_brew_update()
            current_version = get_installed_ver()
            time.sleep(1); start_all()
            notify(f"{msg}\n✅ 全ペインを再起動しました。")
        threading.Thread(target=_upd, daemon=True).start()

    # ── version ──────────────────────────────────────────────────────────────
    elif sub == "version":
        inst   = get_installed_ver()
        latest = get_latest_ver()
        status = "✅ 最新" if (not latest or inst == latest) else f"⚠️ 更新あり → `{latest}`"
        respond(f"インストール済: `{inst}`  {status}")

    # ── orch ─────────────────────────────────────────────────────────────────
    elif sub == "orch" and len(args) == 2:
        respond(orchestrate(args[1]))

    # ── approve / deny ───────────────────────────────────────────────────────
    elif sub == "approve" and len(args) == 2:
        try:
            i = int(args[1]); tmux_send(i, "y"); pending_approvals.discard(i)
            respond(f"✅ Pane {i} ({PANE_LABELS.get(i,'')}) を承認しました")
        except ValueError:
            respond("❌ `/cc approve <N>`")

    elif sub == "deny" and len(args) == 2:
        try:
            i = int(args[1]); tmux_send(i, "n"); pending_approvals.discard(i)
            respond(f"❌ Pane {i} ({PANE_LABELS.get(i,'')}) を拒否しました")
        except ValueError:
            respond("❌ `/cc deny <N>`")

    # ── log ──────────────────────────────────────────────────────────────────
    elif sub == "log" and len(args) == 2:
        try:
            i = int(args[1]); out = tmux_capture(i, 100)
            respond(f"*Pane {i} ({PANE_LABELS.get(i,'')}) ログ*\n```{esc(out[-2000:])}```")
        except ValueError:
            respond("❌ `/cc log <N>`")

    # ── p<N> <指示> ──────────────────────────────────────────────────────────
    elif re.match(r"^p\d+$", sub) and len(args) == 2:
        try:
            i = int(sub[1:]); tmux_send(i, args[1])
            respond(f"📨 Pane {i} ({PANE_LABELS.get(i,'')}) に送信: `{args[1]}`")
        except ValueError:
            respond("❌ `/cc p<N> <指示>`")

    # ── help ─────────────────────────────────────────────────────────────────
    else:
        respond(
            "*Claude Code Bridge v4 コマンド*\n```\n"
            "/cc status                    → 全ペイン状況（cwd表示付き）\n"
            "/cc start <1-7|all>           → Claude Code 起動  ※all=pane1〜4\n"
            "/cc stop  <1-7|all>           → Claude Code 停止\n"
            "/cc restart <1-7|all>         → 再起動\n"
            "/cc sh <N> <コマンド>         → シェルコマンド実行・結果返信\n"
            "/cc get <N> <ファイル名>      → paneNのcwdからSlackにファイル送信\n"
            "/cc update                    → 全停止→brew upgrade→全再起動\n"
            "/cc version                   → バージョン確認\n"
            "/cc orch <指示>               → オーケストレーター経由で全体指示\n"
            "/cc p<N> <指示>               → 特定ペインに直接指示\n"
            "/cc approve <N>              → 承認 (y)\n"
            "/cc deny <N>                 → 拒否 (n)\n"
            "/cc log <N>                  → ログ表示（100行）\n"
            "─────────────────────────────\n"
            "ファイル添付: Slackに添付するだけで ~/Projects/upload/ に保存\n"
            "             メッセージに「pane2」と書けばpane2のcwdに保存\n"
            "```"
        )

# ── ボタンアクション ─────────────────────────────────────────────────────────

@app.action(re.compile(r"choice_(\d+)_(\d+)"))
def on_choice(ack, body, respond):
    """番号選択式の承認ボタン（1/2/3...）"""
    ack()
    val = body["actions"][0]["value"]          # "idx:num"
    idx, num = val.split(":", 1)
    idx = int(idx)
    tmux_send(idx, num)   # 番号を送信（tmux_send内でEnterも送信済み）
    pending_approvals.discard(idx)
    respond(f"📤 Pane {idx} ({PANE_LABELS.get(idx,'')}) に `{num}` を送信しました")

@app.action(re.compile(r"approve_(\d+)"))
def on_approve(ack, body, respond):
    ack()
    i = int(body["actions"][0]["value"])
    tmux_send(i, "y"); pending_approvals.discard(i)
    respond(f"✅ Pane {i} ({PANE_LABELS.get(i,'')}) を承認しました")

@app.action(re.compile(r"deny_(\d+)"))
def on_deny(ack, body, respond):
    ack()
    i = int(body["actions"][0]["value"])
    tmux_send(i, "n"); pending_approvals.discard(i)
    respond(f"❌ Pane {i} ({PANE_LABELS.get(i,'')}) を拒否しました")

@app.action("do_full_update")
def on_full_update(ack, respond):
    ack()
    respond("🔄 全停止 → brew upgrade → 全再起動を開始します...")
    def _upd():
        global current_version
        stop_all(); time.sleep(2)
        msg = do_brew_update()
        current_version = get_installed_ver()
        time.sleep(1); start_all()
        notify(f"{msg}\n✅ 全ペインを再起動しました。")
    threading.Thread(target=_upd, daemon=True).start()

@app.action("skip_update")
def on_skip(ack, respond):
    ack(); respond("⏭ アップデートをスキップしました")

# ── エントリポイント ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=polling_loop, daemon=True).start()
    print("[bridge] Slack Socket Mode 起動中...")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()
