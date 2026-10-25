"""SKILL.md description 压缩。

只改 frontmatter 里的 description 块，其它键原样保留。
刻意不依赖 PyYAML：整个包只用标准库，而这里只需要定位/替换一个扁平键，
自己写一个最小解析器比引入第三方依赖更可控。
默认 dry-run；apply 时写盘并生成 <file>.bak-<stamp> 备份。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .paths import stamp as _stamp

DEFAULT_MAX_LEN = 180
# 句末标点，优先级从高到低（中文句号 > 中文分号 > 英文分号/句号）
SENTENCE_MARKS = ("\u3002", "\uff1b", "; ", ". ")
# 截断后要剪掉的尾部标点，避免出现「悬空标点」
PUNCT = "\uff0c,;\uff1b\u3002:\uff1a\u3001\u2014\u2013- \t\u3000!?\uff01\uff1f"
_TOP_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
_DESC_RE = re.compile(r"^description:[ \t]*(.*)$")


def compress(text, max_len=DEFAULT_MAX_LEN):
    """把 description 压到 <= max_len 字符。

    优先断在句末标点；没有合适句末时按 max_len 切，并对英文回退到词边界，
    最后剪掉尾部悬空标点。**这是一个有损近似**，不做语义判断。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    cut = -1
    for mark in SENTENCE_MARKS:
        idx = s.find(mark, 40)
        if idx > 0:
            end = idx + len(mark)
            if end <= max_len:
                cut = end
                break
    if cut < 0:
        cut = _word_boundary(s, max_len)
    out = s[:cut].rstrip(PUNCT)
    if not out:
        out = s[:max_len].rstrip(PUNCT) or s[:max_len]
    return out


def _word_boundary(s, limit):
    """若 limit 落在英文单词中间，往回退到最近的空格；找不到就按 limit 切。

    只保护 ASCII 字母数字：中日韩没有词间空格，在任意字符处截断都不会产生
    「半个词」，硬退反而会丢内容。
    """
    limit = min(limit, len(s))
    if limit >= len(s) or limit <= 0:
        return len(s)
    prev_ch, next_ch = s[limit - 1], s[limit]
    if prev_ch.isascii() and prev_ch.isalnum() and next_ch.isascii() and next_ch.isalnum():
        back = s.rfind(" ", 40, limit)
        if back > 0:
            return back
    return limit


def split_frontmatter(text):
    """拆出 frontmatter 的原文与边界。

    返回 (fm_text, body_start, close_start, close_end) 或 None。
    fm_text 含首尾之间的内容（通常以换行结尾）。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return None
    first_nl = text.find("\n")
    if first_nl < 0 or text[:first_nl].strip() != "---":
        return None
    body_start = first_nl + 1
    m = re.search(r"(?m)^---[ \t]*\r?$", text[body_start:])
    if not m:
        return None
    close_start = body_start + m.start()
    close_end = body_start + m.end()
    return text[body_start:close_start], body_start, close_start, close_end


def parse_description(fm_text):
    """从 frontmatter 取 description 的值（支持单行/引号/块标量）。

    返回 (value, line_index)；找不到返回 (None, -1)。
    """
    lines = str(fm_text or "").replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        m = _DESC_RE.match(line)
        if not m:
            continue
        raw = m.group(1).strip()
        if raw in (">", "|", ">-", "|-", ">+", "|+"):
            parts = []
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    parts.append("")
                    continue
                if not nxt[:1].isspace():
                    break
                parts.append(nxt.strip())
            joiner = " " if raw.startswith(">") else "\n"
            raw = joiner.join(parts).strip()
        elif len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            if raw[0] == '"':
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = raw[1:-1]
            else:
                raw = raw[1:-1]
        return raw.strip(), i
    return None, -1


def _entry(name, path, old, new, error=None):
    old = "" if old is None else str(old)
    new = "" if new is None else str(new)
    return {
        "name": name,
        "path": str(path),
        "old": old,
        "new": new,
        "old_len": len(old),
        "new_len": len(new),
        "changed": bool(old) and new != old,
        "error": error,
    }


def plan(root, max_len=DEFAULT_MAX_LEN):
    """扫描 <root>/<skill>/SKILL.md，返回压缩计划（不写盘）。"""
    root = Path(root).expanduser()
    entries = []
    if not root.is_dir():
        return entries
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            entries.append(_entry(child.name, md, None, None, "read-error: %s" % exc))
            continue
        split = split_frontmatter(text)
        if not split:
            entries.append(_entry(child.name, md, None, None, "no-frontmatter"))
            continue
        old, _idx = parse_description(split[0])
        if old is None:
            entries.append(_entry(child.name, md, None, None, "no-description"))
            continue
        entries.append(_entry(child.name, md, old, compress(old, max_len)))
    return entries


def _replace_description(text, new_value):
    """把 description 块替换成单个 JSON 字符串行；失败返回 None。"""
    split = split_frontmatter(text)
    if not split:
        return None
    fm_text, body_start, close_start, _close_end = split
    lines = fm_text.replace("\r\n", "\n").rstrip("\n").split("\n")
    idx = next((i for i, line in enumerate(lines) if _DESC_RE.match(line)), -1)
    if idx < 0:
        return None
    stop = idx + 1
    while stop < len(lines) and not _TOP_KEY_RE.match(lines[stop]):
        stop += 1
    new_lines = lines[:idx] + ["description: " + json.dumps(new_value, ensure_ascii=False)] + lines[stop:]
    new_fm = "\n".join(new_lines).rstrip("\n") + "\n"
    return text[:body_start] + new_fm + text[close_start:]


def _valid_frontmatter(text, expect):
    """替换后的轻量结构校验（只认扁平键，不做完整 YAML 解析）。"""
    split = split_frontmatter(text)
    if not split:
        return False
    fm_text = split[0]
    desc, _idx = parse_description(fm_text)
    if desc != expect:
        return False
    for line in fm_text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        if line[:1].isspace() or line.lstrip().startswith("#"):
            continue
        if not _TOP_KEY_RE.match(line):
            return False
    return True


def apply(entries, stamp=None):
    """执行 plan 的结果：写盘并生成 <file>.bak-<stamp> 备份。

    返回 {"written": [...], "backups": [路径], "skipped": [...], "errors": [...]}。
    只有 changed=True 且无 error 的条目会被写；备份只在真正写之前创建。
    """
    result = {"written": [], "backups": [], "skipped": [], "errors": []}
    if not stamp:
        stamp = _stamp()
    for entry in entries or []:
        path = Path(entry.get("path", ""))
        name = entry.get("name")
        if entry.get("error"):
            result["errors"].append({"name": name, "path": str(path), "error": entry["error"]})
            continue
        if not entry.get("changed"):
            result["skipped"].append({"name": name, "path": str(path), "reason": "unchanged"})
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result["errors"].append({"name": name, "path": str(path), "error": "read-error: %s" % exc})
            continue
        new_text = _replace_description(text, entry["new"])
        if new_text is None or not _valid_frontmatter(new_text, entry["new"]):
            result["errors"].append({"name": name, "path": str(path), "error": "frontmatter-rewrite-failed"})
            continue
        bak = path.with_name(path.name + ".bak-" + stamp)
        if not bak.exists():
            shutil.copy2(path, bak)
            result["backups"].append(str(bak))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
        result["written"].append({
            "name": name,
            "path": str(path),
            "old_len": entry.get("old_len"),
            "new_len": entry.get("new_len"),
            "backup": str(bak),
        })
    return result
