"""六层静态上下文基线快照：规则 / 记忆 / 技能 / 工具面 / 会话 / 知识库（+ 可选成本）。

只读、不写盘（写文件由 CLI 的 --out 决定）。
估算口径：中文 2 字符/token、英文 3.5 字符/token，都是**估算**，只用于横向比较。
工具 schema 的真实 token 需要引擎侧 debug 输出；这里标 None，不编数。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .memory import CN_CHARS_PER_TOKEN, EN_CHARS_PER_TOKEN, collect_files, estimate_tokens, load_store
from .skills import parse_description, split_frontmatter


def _size(path):
    p = Path(path)
    try:
        return p.stat().st_size if p.is_file() else None
    except OSError:
        return None


def _agents_layer(home, workspace=None):
    """L1 规则：全局与项目 AGENTS.md 的字节数。"""
    home = Path(home)
    out = {
        "global_agents_path": str(home / "AGENTS.md"),
        "global_agents_bytes": _size(home / "AGENTS.md"),
        "project_agents_bytes": None,
    }
    if workspace:
        ws = Path(workspace)
        out["project_agents_path"] = str(ws / "AGENTS.md")
        out["project_agents_bytes"] = _size(ws / "AGENTS.md")
    else:
        out["note"] = "未提供 --workspace"
    return out


def _memory_layer(home):
    """L2 记忆：条目数 / 字符数 / token 估算 / 头部文件。"""
    files = collect_files(home)
    items = 0
    chars = 0
    per = []
    for scope, path in files:
        _doc, its = load_store(path)
        c = sum(len(str(i.get("content", ""))) for i in its)
        items += len(its)
        chars += c
        per.append({"scope": scope, "file": str(path), "items": len(its), "chars": c})
    per.sort(key=lambda r: -r["chars"])
    return {
        "files": len(files),
        "items": items,
        "chars": chars,
        "est_tokens_cn": estimate_tokens(chars, cn=True),
        "top_files": per[:10],
    }


def _skills_layer(home):
    """L3 技能：只量「常驻注入」的元数据（目录名 + description），不读正文。"""
    root = Path(home) / "skills"
    children = sorted(root.iterdir(), key=lambda p: p.name) if root.is_dir() else []
    count = 0
    chars = 0
    for child in children:
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        count += 1
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        desc = ""
        split = split_frontmatter(text)
        if split:
            desc, _idx = parse_description(split[0])
            desc = desc or ""
        chars += len(child.name) + len(desc)
    return {"count": count, "metadata_chars": chars, "est_tokens": estimate_tokens(chars, cn=False)}


def _cordis_layer(home):
    """L4 工具面（配置侧）：插件条目 / 停用数 / 常驻 MCP。缺失即空。"""
    path = Path(home) / "profiles" / "web" / "cordis.patch.yml"
    out = {"file": str(path), "present": path.is_file(), "entries": 0, "disabled": 0, "mcp": []}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    blocks = re.split(r"\n(?=\s*[-]\s+id:\s)", text)
    for block in blocks:
        m = re.search(r"^\s*[-]\s+id:\s*([\w.-]+)", block, re.M)
        if not m:
            continue
        out["entries"] += 1
        disabled = bool(re.search(r"^\s+disabled:\s*true", block, re.M))
        if disabled:
            out["disabled"] += 1
        if m.group(1).startswith("mcp-"):
            out["mcp"].append({"id": m.group(1), "disabled": disabled})
    out["mcp_enabled"] = sum(1 for x in out["mcp"] if not x["disabled"])
    return out


def _sessions_layer(home):
    """L5 会话：日志文件数与磁盘占用（只统计，不解析）。"""
    root = Path(home) / "sessions"
    files = 0
    total = 0
    if root.is_dir():
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    return {"files": files, "bytes": total, "mb": round(total / 1048576, 1)}


def _kb_layer(kb):
    """L6 知识库：只读打开 sqlite，数表与几个常见表的行数。"""
    if not kb:
        return {"db": None, "note": "未提供 --kb；知识库层计为未知"}
    path = Path(kb).expanduser()
    if path.is_dir():
        path = path / "index" / "kb.sqlite"
    out = {"db": str(path), "bytes": _size(path), "tables": []}
    if not path.is_file():
        out["note"] = "知识库文件不存在"
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % quote(str(path)), uri=True)
        try:
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            out["tables"] = tables[:10]
            for name in ("pages", "chunks", "documents"):
                if name in tables:
                    out[name] = con.execute("select count(*) from %s" % name).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    return out


def _cost_layer(home):
    """可选成本层：如果有 usage 汇总文件就读出来；没有就空。"""
    path = Path(home) / ".dshw-usage.json"
    if not path.is_file():
        return {}
    try:
        usage = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(usage, dict):
        return {}
    history = usage.get("history") or {}
    try:
        history_sum = round(sum(v for v in history.values() if isinstance(v, (int, float))), 2)
    except TypeError:
        history_sum = None
    events = [
        e.get("tokens", 0)
        for e in usage.get("events", [])
        if isinstance(e, dict) and e.get("tokens")
    ]
    return {
        "balance": usage.get("lastBalance"),
        "today": usage.get("todayUsage"),
        "days": len(history),
        "sum_history": history_sum,
        "min_turn_tokens": min(events) if events else None,
    }


def snapshot(home, workspace=None, kb=None, cost=False):
    """生成六层快照（dict）。纯读取，无副作用。"""
    home = Path(home).expanduser()
    snap = {
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "home": str(home),
        "workspace": str(Path(workspace).expanduser()) if workspace else None,
        "kb": str(kb) if kb else None,
        "platform": platform.system(),
        "python": platform.python_version(),
        "L1_rules": _agents_layer(home, workspace),
        "L2_memory": _memory_layer(home),
        "L3_skills": _skills_layer(home),
        "L4_tools": {
            "cordis": _cordis_layer(home),
            "tool_schema_tokens": None,
            "note": "真实 schema token 需要引擎侧 debug 输出；这里不估算、不编数。",
        },
        "L5_sessions": _sessions_layer(home),
        "L6_kb": _kb_layer(kb),
        "estimate": {
            "cn_chars_per_token": CN_CHARS_PER_TOKEN,
            "en_chars_per_token": EN_CHARS_PER_TOKEN,
            "note": "字符/token 是经验估算，仅用于横向比较，不等于供应商计费。",
        },
    }
    if cost:
        snap["X2_cost"] = _cost_layer(home)
    return snap
