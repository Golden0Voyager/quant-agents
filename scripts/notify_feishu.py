#!/usr/bin/env python3
"""
Notify Feishu (local + CI)
==========================
复用 .github/workflows/daily-analysis.yml 里 Notify Feishu 步骤的逻辑,
支持从本地 reports/<YYYYMMDD_batch_*>/ 目录构造 summary + 发送飞书卡片,
并把每只 ticker 的 complete_report.md 转 docx 上传到飞书群.

Usage:
    # Dry-run (默认,只打印 payload,不实际发)
    uv run python scripts/notify_feishu.py

    # 实际发送 (需要 env 中有 FEISHU_WEBHOOK_URL)
    uv run python scripts/notify_feishu.py --send

    # 只发送卡片,不传文件
    uv run python scripts/notify_feishu.py --send --skip-files

    # 指定 batch 目录
    uv run python scripts/notify_feishu.py --batch-dir reports/20260703_batch_my --send

Environment:
    FEISHU_WEBHOOK_URL   飞书机器人 webhook (必需,卡片发送)
    FEISHU_APP_ID        飞书 app id (文件上传必需)
    FEISHU_APP_SECRET    飞书 app secret (文件上传必需)
    CHAT_ID              飞书 chat id (默认 oc_6a477c033c31899f3212fa0dd5f10dde)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHAT_ID = "oc_6a477c033c31899f3212fa0dd5f10dde"

# 飞书 markdown 卡片对单字段 content 长度有限 (~25k 字符),保留余量
SUMMARY_MAX_CHARS = 25_000


# ---------------------------------------------------------------------------
# Summary 构造 (对应 workflow 里的 Generate combined summary 段)
# ---------------------------------------------------------------------------

def _extract_field(md_text: str, key: str) -> str:
    """从 decision.md 抽取 **Key**: value 形式的 value.
    兼容两种格式:
      - **Rating**: Underweight    (冒号在 ** 外)
      - **评级：Hold（持有）**          (冒号在 ** 内)
    """
    aliases = _key_aliases.get(key, ())
    keys_pattern = "|".join(re.escape(k) for k in (key, *aliases) if k)
    # 先试冒号在 ** 外: **Key**: value
    m = re.search(rf"\*\*(?:{keys_pattern})\*\*\s*[:：]\s*([^\n]+)", md_text)
    if m:
        return m.group(1).strip().strip("*").strip()
    # 再试冒号在 ** 内: **Key**: value** (value 在 ** 内)
    m = re.search(rf"\*\*(?:{keys_pattern})\s*[:：]\s*([^*]+?)\*\*", md_text)
    if m:
        return m.group(1).strip().strip("*").strip()
    return ""


# key 英文名 -> 中文别名映射
_key_aliases: dict[str, tuple[str, ...]] = {
    "Rating": ("评级", "建议评级"),
    "Entry": ("入场", "建仓价", "Entry Price"),
    "Stop": ("止损", "Stop Loss"),
    "Price Target": ("目标价", "目标"),
    "Size": ("仓位", "建议仓位"),
    "Executive Summary": ("执行摘要", "理由", "决策摘要"),
}


def _build_summary(latest_batch: Path) -> str:
    """遍历 latest_batch 下的 ticker 目录,生成飞书 summary markdown."""
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    lines = [f"# 每日分析结论 - {date}", ""]

    if not latest_batch or not latest_batch.is_dir():
        return "\n".join(lines)

    for ticker_dir in sorted(latest_batch.iterdir()):
        if not ticker_dir.is_dir():
            continue
        # Decision file lives at <ticker>/5_portfolio/decision.md
        decision = ticker_dir / "5_portfolio" / "decision.md"
        if not decision.is_file():
            # legacy fallback for older runs
            legacy = ticker_dir / "reports" / "final_trade_decision.md"
            if legacy.is_file():
                decision = legacy
            else:
                continue
        text = decision.read_text(encoding="utf-8")

        rating = _extract_field(text, "Rating")
        entry = _extract_field(text, "Entry")
        stop = _extract_field(text, "Stop")
        target = _extract_field(text, "Price Target")
        size = _extract_field(text, "Size")
        summary = _extract_field(text, "Executive Summary")

        ticker_name = ticker_dir.name
        lines.append(f"**{ticker_name}**")
        lines.append(f"- 评级: {rating or '—'}")
        lines.append(f"- 入场: {entry or '—'}")
        lines.append(f"- 止损: {stop or '—'}")
        lines.append(f"- 目标: {target or '—'}")
        lines.append(f"- 仓位: {size or '—'}")
        if summary:
            lines.append(f"- 理由: {summary[:100]}...")
        lines.append("")

    return "\n".join(lines)


def _pick_latest_batch(combined_root: Path, list_name: str) -> Path | None:
    """从 reports/<list_name>/<YYYYMMDD_batch_*/> 选最新一个."""
    pattern = re.compile(r"^\d{8}_batch_")
    candidates = [p for p in (combined_root / list_name).iterdir() if p.is_dir() and pattern.match(p.name)]
    return max(candidates, key=lambda p: p.name) if candidates else None


# ---------------------------------------------------------------------------
# 飞书交互
# ---------------------------------------------------------------------------

def _build_card_payload(*, overall: str, date: str, run_url: str,
                        my_status: str, erin_status: str,
                        summary: str, release_url: str) -> dict:
    """构造飞书 interactive card payload (与 workflow 同结构)."""
    if overall == "success":
        color, title = "green", f"✅ 每日分析完成 - {date}"
    else:
        color, title = "red", f"❌ 每日分析异常 - {date}"

    status_line = f"**我的 list**: {my_status} | **Erin list**: {erin_status}"
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": f"**运行**: [查看详情]({run_url})\n{status_line}"},
                {"tag": "hr"},
                {"tag": "markdown", "content": summary[:SUMMARY_MAX_CHARS]},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "📥 下载完整报告"},
                     "url": release_url, "type": "primary"},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "📊 查看 Actions"},
                     "url": run_url, "type": "default"},
                ]},
            ],
        },
    }


def _post_json(url: str, payload: dict, *, token: str | None = None) -> tuple[int, dict]:
    """POST JSON. 返回 (status_code, response_body)."""
    import urllib.request
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def _upload_file(token: str, file_path: Path, file_name: str, file_type: str) -> str:
    """上传文件到飞书 im/v1/files, 返回 file_key."""
    import urllib.request
    import uuid
    boundary = uuid.uuid4().hex
    body = []
    for k, v in [("file_type", file_type), ("file_name", file_name)]:
        body.append(f"--{boundary}\r\n".encode())
        body.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.append(f"{v}\r\n".encode())
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode()
    )
    body.append(b"Content-Type: application/octet-stream\r\n\r\n")
    body.append(file_path.read_bytes())
    body.append(f"\r\n--{boundary}--\r\n".encode())
    payload = b"".join(body)

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/files", data=payload, method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", {}).get("file_key", "")
    except Exception as e:
        print(f"  ! upload failed: {e}", file=sys.stderr)
        return ""


def _send_file_message(token: str, chat_id: str, file_key: str) -> bool:
    """把 file_key 发送到 chat."""
    status, body = _post_json(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}),
        },
        token=token,
    )
    return status == 200 and body.get("code") == 0


def _get_tenant_token(app_id: str, app_secret: str) -> str:
    status, body = _post_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    return body.get("tenant_access_token", "") if status == 200 else ""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--send", action="store_true", help="实际发送 (默认 dry-run)")
    p.add_argument("--skip-files", action="store_true", help="只发卡片,不上传文件")
    p.add_argument("--batch-dir", type=Path, help="指定单个 batch 目录,跳过自动探测")
    p.add_argument("--chat-id", default=os.environ.get("CHAT_ID", DEFAULT_CHAT_ID))
    p.add_argument("--my-dir", type=Path, default=REPO_ROOT / "reports" / "my",
                   help="analyze-my 解压后的根目录")
    p.add_argument("--erin-dir", type=Path, default=REPO_ROOT / "reports" / "erin",
                   help="analyze-erin 解压后的根目录")
    p.add_argument("--run-url", default=os.environ.get("RUN_URL",
                   "https://github.com/Golden0Voyager/Trading-Agents-A-Share/actions"))
    p.add_argument("--release-url", default=os.environ.get("RELEASE_URL",
                   "https://github.com/Golden0Voyager/Trading-Agents-A-Share/releases"))
    p.add_argument("--summary-only", action="store_true",
                   help="只输出 summary markdown 到 stdout,不发送;供 CI 使用")
    args = p.parse_args()

    # 1) 选 latest batch 并构造 summary
    if args.batch_dir:
        latest = args.batch_dir
    else:
        latest_my = _pick_latest_batch(args.my_dir, "")
        latest_erin = _pick_latest_batch(args.erin_dir, "")
        latest = latest_my or latest_erin

    print(f"[info] latest batch: {latest}", file=sys.stderr)
    summary = _build_summary(latest) if latest else "# 每日分析\n\n(无可用报告)"
    print(f"[info] summary chars: {len(summary)}", file=sys.stderr)

    # --summary-only: emit markdown to stdout, skip everything else
    if args.summary_only:
        print(summary)
        return 0

    # 2) 拼装 payload
    date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    payload = _build_card_payload(
        overall="success",
        date=date,
        run_url=args.run_url,
        my_status="success",
        erin_status="success",
        summary=summary,
        release_url=args.release_url,
    )

    webhook = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not args.send:
        print("\n[dry-run] would POST payload to FEISHU_WEBHOOK_URL:")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        if not args.skip_files and latest:
            print(f"\n[dry-run] would upload {len(list(latest.iterdir()))} ticker files")
        return 0

    if not webhook:
        print("[error] FEISHU_WEBHOOK_URL not set in env", file=sys.stderr)
        return 2

    # 3) 发卡片
    status, body = _post_json(webhook, payload)
    print(f"[card] POST -> status={status} code={body.get('code')} msg={body.get('msg')}")
    if status != 200 or body.get("code") != 0:
        print(f"[error] card send failed: {body}", file=sys.stderr)
        # 卡片失败不致命,继续尝试文件上传

    # 4) 上传文件
    if args.skip_files or not latest:
        return 0

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not (app_id and app_secret):
        print("[warn] FEISHU_APP_ID/FEISHU_APP_SECRET missing, skip file upload", file=sys.stderr)
        return 0

    token = _get_tenant_token(app_id, app_secret)
    if not token:
        print("[error] failed to get tenant_access_token", file=sys.stderr)
        return 3

    sent = 0
    for ticker_dir in sorted(latest.iterdir()):
        if not ticker_dir.is_dir():
            continue
        report = ticker_dir / "complete_report.md"
        if not report.is_file():
            continue
        ticker_name = ticker_dir.name

        # 直接传 md, 不转 docx
        upload, upload_name, ftype = report, f"{ticker_name}_{date}.md", "stream"
        file_key = _upload_file(token, upload, upload_name, ftype)
        if file_key and _send_file_message(token, args.chat_id, file_key):
            print(f"[file] sent: {upload_name}")
            sent += 1
        else:
            print(f"[file] FAILED: {upload_name}", file=sys.stderr)

    print(f"\n[done] {sent} file(s) sent to chat {args.chat_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
