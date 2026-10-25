"""从会话日志里挖真实的工具面与静态上下文（只读，不调用模型）。

为什么不是估算：日志里有两类真实数据 ——
  1) request/header.tools          这一次请求实际带上的工具数组（name/description/parameters）；
  2) assistant/message.data.usage  供应商回传的 inputTokens / cacheReadTokens
     （真正 prompt = inputTokens + cacheReadTokens）。

已知限制（老实写在这里，别在报告里装作测到了）：
  * request/header 里的 description 是被日志**截断**的存法，长描述（例如内嵌的 SDK 声明）
    不会完整落盘 => tools_chars 只是下界，真实 token 需要引擎侧 debug 输出；
  * system/message 的 content 在日志里是空的（脱敏/不落盘）=> system prompt 正文无法直接量；
  * 因此「静态上下文」只有在**冷启动首步**（cacheReadTokens=0 且首条 usage 出现在首轮）
    才可推算：static ~= prompt1 - 首条用户消息 tokens。算不出就输出 None，绝不编数。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .memory import estimate_tokens


def text_len(content):
    """用户消息正文长度：字符串或 [{type,text}] 列表。"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(x.get("text") or "") for x in content if isinstance(x, dict))
    return 0


def read_rows(path):
    """读取日志行。返回 (rows, skip_reason)。

    .jsonl 直接读；.jsonl.zstd 需要 zstdcat（或 zstd -dc），缺失就跳过并说明。
    """
    path = Path(path)
    name = path.name.lower()
    raw = None
    if name.endswith(".zstd") or name.endswith(".zst"):
        exe = shutil.which("zstdcat") or shutil.which("zstd")
        if not exe:
            return [], "缺少 zstdcat/zstd，跳过压缩日志"
        cmd = [exe, str(path)] if os.path.basename(exe) == "zstdcat" else [exe, "-dc", str(path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=40)
        except (OSError, subprocess.SubprocessError) as exc:
            return [], "解压失败: %s" % exc
        raw = proc.stdout
    else:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return [], "读取失败: %s" % exc
    rows = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, None


def analyse_rows(rows):
    """纯函数：一组日志行 -> 一条会话的结构化指标；拿不到 usage 返回 None。"""
    if not rows or rows[0].get("type") != "session":
        return None
    first_usage = None          # (seq, usage)
    first_user_chars = None
    first_user_seq = None
    users = 0
    users_before_usage = 0      # 首条 usage 之前有几条用户消息（真冷启动应 <=1）
    assistants_before_usage = 0
    header = None
    for row in rows:
        rtype = row.get("type")
        data = row.get("data") or {}
        if rtype == "user/message":
            users += 1
            if first_usage is None:
                users_before_usage += 1
            if first_user_chars is None:
                first_user_chars = text_len(data.get("content"))
                first_user_seq = row.get("seq")
        if rtype == "assistant/message" and first_usage is None:
            assistants_before_usage += 1
            usage = data.get("usage")
            if usage:
                first_usage = (row.get("seq"), usage)
        if rtype == "request/header":
            header = data.get("header") or header
    if first_usage is None:
        return None
    seq_u, usage = first_usage
    prompt = (usage.get("inputTokens") or 0) + (usage.get("cacheReadTokens") or 0)
    # 真·冷启动：cacheRead=0，且首条 usage 出现在首条用户消息之后（之前 <=1 条用户消息、<=1 次 assistant 轮次）
    clean = (
        (usage.get("cacheReadTokens") or 0) == 0
        and users_before_usage <= 1
        and assistants_before_usage <= 1
    )
    static = None
    if clean and first_user_chars is not None:
        static = max(prompt - estimate_tokens(first_user_chars, cn=True), 0)
    tools = (header or {}).get("tools") or []
    tools_chars = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    return {
        "session": rows[0].get("id"),
        "cwd": rows[0].get("cwd"),
        "users": users,
        "first_user_seq": first_user_seq,
        "first_usage_seq": seq_u,
        "prompt1": prompt,
        "clean_cold_start": bool(clean),
        "static_est": static,
        "tools_count": len(tools),
        "tools_chars": tools_chars,
        "tools_tokens_est": estimate_tokens(tools_chars, cn=False) if tools_chars else None,
    }


def parse_since(value, now=None):
    """'7d' / '24h' -> 截止时间戳；None 原样返回；非法抛 ValueError。"""
    if value is None or value == "":
        return None
    m = re.fullmatch(r"(\d+)([dh])", str(value).strip())
    if not m:
        raise ValueError("--since 只接受 <数字>d 或 <数字>h，例如 7d、24h")
    count = int(m.group(1))
    seconds = count * 86400 if m.group(2) == "d" else count * 3600
    return (now if now is not None else time.time()) - seconds


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def scan(home, since=None, now=None):
    """扫描 <home>/sessions 下的会话日志，返回汇总。

    .jsonl 直接读；.jsonl.zstd 需要 zstdcat/zstd，缺失时跳过并计数说明。
    """
    home = Path(home).expanduser()
    sessions = home / "sessions"
    cutoff = parse_since(since, now=now)
    files = []
    if sessions.is_dir():
        for dirpath, _dirs, names in os.walk(sessions):
            for name in names:
                if name.startswith("session") and "jsonl" in name:
                    files.append(Path(dirpath) / name)
    files.sort(key=_mtime)

    records = []
    skipped_zstd = 0
    skipped_error = 0
    for path in files:
        if cutoff is not None and _mtime(path) < cutoff:
            continue
        rows, reason = read_rows(path)
        if reason:
            if "zstd" in reason:
                skipped_zstd += 1
            else:
                skipped_error += 1
            continue
        rec = analyse_rows(rows)
        if not rec:
            continue
        rec["file"] = path.parent.name
        rec["path"] = str(path)
        rec["mtime_ts"] = _mtime(path)
        rec["mtime"] = _dt.datetime.fromtimestamp(rec["mtime_ts"]).strftime("%Y-%m-%d %H:%M")
        records.append(rec)

    clean = [r for r in records if r["clean_cold_start"]]
    clean.sort(key=lambda r: r["mtime_ts"])
    records.sort(key=lambda r: r["mtime_ts"])
    latest = records[-1] if records else None
    return {
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "home": str(home),
        "since": since,
        "sessions_scanned": len(records),
        "clean_samples": len(clean),
        "latest_tools_surface": (
            {k: latest[k] for k in ("session", "file", "mtime", "tools_count", "tools_chars", "tools_tokens_est")}
            if latest else None
        ),
        "last_clean": clean[-5:],
        "records": records[-50:],
        "skipped": {"zstd_unavailable": skipped_zstd, "read_errors": skipped_error},
        "note": (
            "static_est 只有冷启动首步（cacheRead=0）可算；tools_chars 是日志落盘的下界"
            "（长 description 会被截断）；system prompt 不落盘。"
        ),
    }
