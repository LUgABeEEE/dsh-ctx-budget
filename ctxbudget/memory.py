"""记忆库体检 / 打分 / 分层归档。

设计要点（照搬生产版的结论，别改成「按年龄切」）：

  * 很多 agent home 的记忆是短时间内批量抽取进来的，createdAt 分不开价值；
  * 真正区分价值的是 useCount / updatedAt（被强化过）/ type / 是否含时效性措辞；
  * 规范性措辞（必须 / 禁止 / 红线 …）是长期约束，无论分数多低都必须留在主库。

归档不是删除：移出的条目原样写进 memory/archive/projects/<key>-<stamp>.json，
主库只留一条「档案索引」指针条目指路，不编造结论。
"""
from __future__ import annotations

import collections
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from .paths import ensure_dir

# 估算口径（**估算**，不是供应商计费）：中文约 2 字符/token，英文约 3.5 字符/token。
CN_CHARS_PER_TOKEN = 2.0
EN_CHARS_PER_TOKEN = 3.5

OVER_ITEMS = 300        # 单文件条目超限阈值
OVER_TOKENS = 20000     # 单文件 token 估算超限阈值
STALE_DAYS = 90         # 只读体检里的「超龄」阈值
BUDGET = 288            # 主库保留硬上限：给最多 12 条指针留位置 => 总数 <= 300
MAX_POINTERS = 12
DEFAULT_KEEP = 280

NORMATIVE = re.compile(r"必须|禁止|不得|勿|红线|一定要|切记|绝不要")
EPHEMERAL = re.compile(
    r"(剩|还有)\s*\d+\s*(天|小时|分钟)|今天|今日|明天|昨晚|刚才|目前|当前|现在|本周|这周|昨天"
)


def estimate_tokens(chars, cn=True):
    """字符数 -> token 估算（**估算**）。"""
    return round((chars or 0) / (CN_CHARS_PER_TOKEN if cn else EN_CHARS_PER_TOKEN))


def norm(text):
    """归一化：去掉所有空白并小写，用于重复内容检测。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def digest(text):
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:12]


def load_store(path):
    """读一个 memory store；坏文件返回 ({}, [])，不猜测内容。"""
    path = Path(path).expanduser()
    try:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}, []
    items = doc.get("items", []) if isinstance(doc, dict) else []
    if not isinstance(items, list):
        items = []
    return doc, items


def collect_files(home):
    """列出记忆库文件：global + projects/*.json（排序，输出确定）。"""
    home = Path(home).expanduser()
    files = [("global", home / "memory" / "global.json")]
    pdir = home / "memory" / "projects"
    if pdir.is_dir():
        for f in sorted(pdir.iterdir(), key=lambda p: p.name):
            if f.suffix == ".json" and f.is_file():
                files.append(("projects/" + f.stem, f))
    return files


def audit(home, now=None):
    """只读体检：分布 / 无 tags / 重复 / 超龄 / 超限。不写任何文件。"""
    now_ms = now if now is not None else time.time() * 1000
    day = 86400 * 1000
    rows = []
    dup = collections.defaultdict(list)
    stale = []
    no_tags = 0
    total = 0
    files = collect_files(home)
    for scope, path in files:
        _doc, items = load_store(path)
        chars = sum(len(str(i.get("content", ""))) for i in items)
        total += len(items)
        rows.append({
            "scope": scope,
            "file": str(path),
            "items": len(items),
            "chars": chars,
            "est_tokens_cn": estimate_tokens(chars, cn=True),
        })
        for item in items:
            content = str(item.get("content", ""))
            if not item.get("tags"):
                no_tags += 1
            created = item.get("createdAt") or 0
            if created and (now_ms - created) > STALE_DAYS * day:
                stale.append({
                    "id": item.get("id"),
                    "scope": scope,
                    "age_days": round((now_ms - created) / day),
                })
            if content:
                dup[digest(content)].append({
                    "id": item.get("id"),
                    "scope": scope,
                    "len": len(content),
                })
    duplicates = {k: v for k, v in dup.items() if len(v) > 1}
    over = [r for r in rows if r["items"] > OVER_ITEMS or r["est_tokens_cn"] > OVER_TOKENS]
    rows.sort(key=lambda r: -r["est_tokens_cn"])
    return {
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "home": str(Path(home).expanduser()),
        "files": len(files),
        "total": total,
        "no_tags": no_tags,
        "duplicates": duplicates,
        "dup_group_count": len(duplicates),
        "stale": stale,
        "over": over,
        "schema_note": (
            "字段以 id/scope/content/type/tags/links/origin/status/createdAt/updatedAt/useCount 为准；"
            "没有 title/ttlDays/supersedes"
        ),
        "rows": rows,
    }


def score(item, tag_freq, now):
    """给一条记忆打分（越大越该留）。now 单位是毫秒。

    为什么不是按年龄归档：本工具来自的生产库里，大量条目是短期批量抽取的，
    createdAt 几乎一样；能区分价值的是「被用过 / 被置顶 / 类型 / 时效性措辞」。
    """
    s = 0.0
    s += 6.0 * (item.get("useCount") or 0)
    if item.get("pinned"):
        s += 5
    s += {"preference": 4, "decision": 3, "pattern": 2, "entity": 1}.get(item.get("type"), 0)
    updated, created = item.get("updatedAt") or 0, item.get("createdAt") or 0
    if updated > created + 1000:
        s += 3
        age_days = (now - updated) / 86400000.0
    else:
        age_days = (now - created) / 86400000.0
    s += max(0.0, 8.0 - age_days / 4.0)
    content = str(item.get("content") or "")
    if len(content) < 40:
        s -= 2.5
    if len(content) > 200:
        s += 0.5
    if item.get("links"):
        s += 1
    if NORMATIVE.search(content):
        s += 5.0        # 规范性措辞 = 长期有效，别归档
    if EPHEMERAL.search(content) and not (item.get("useCount") or 0):
        s -= 4
    for tag in (item.get("tags") or []):
        s += 1.2 / (tag_freq.get(tag, 1) ** 0.5)
    return s


def plan(project_path, keep_n=DEFAULT_KEEP, now=None):
    """计算一个项目的保留/移出方案（不写盘）。

    保留顺序：
      1) 必留：useCount>0 / pinned / 含规范性措辞；
      2) 主题覆盖：每个热 tag 至少留 3 条（>=10 次）或 1 条；
      3) 按分数补到 keep_n；
      4) 硬上限 BUDGET（给指针留位置），只从非必留条目里裁。
    """
    doc, items = load_store(project_path)
    now_ms = now if now is not None else time.time() * 1000
    tag_freq = collections.Counter()
    for item in items:
        for tag in item.get("tags") or []:
            tag_freq[tag] += 1
    scored = sorted(items, key=lambda i: (-score(i, tag_freq, now_ms), str(i.get("id"))))
    keep, used, mandatory = [], set(), set()
    # 1) 必留
    for item in items:
        content = str(item.get("content") or "")
        if (item.get("useCount") or 0) > 0 or item.get("pinned") or NORMATIVE.search(content):
            keep.append(item)
            used.add(item.get("id"))
            mandatory.add(item.get("id"))
    # 2) 主题覆盖
    for tag, _n in tag_freq.most_common(25):
        want = 3 if tag_freq[tag] >= 10 else 1
        count = 0
        for item in scored:
            if item.get("id") in used:
                continue
            if tag in (item.get("tags") or []):
                keep.append(item)
                used.add(item.get("id"))
                count += 1
                if count >= want:
                    break
    # 3) 按分数补到 keep_n
    for item in scored:
        if len(keep) >= keep_n:
            break
        if item.get("id") in used:
            continue
        keep.append(item)
        used.add(item.get("id"))
    # 4) 硬上限：从尾部裁非必留（必留永不裁）
    if len(keep) > BUDGET:
        tail = [i for i in reversed(keep) if i.get("id") not in mandatory]
        for item in tail[:len(keep) - BUDGET]:
            keep.remove(item)
            used.discard(item.get("id"))
    drop = [i for i in items if i.get("id") not in used]
    return {
        "doc": doc,
        "items": items,
        "keep": keep,
        "drop": drop,
        "tag_freq": tag_freq,
        "mandatory": mandatory,
        "budget": BUDGET,
        "keep_n": keep_n,
    }


def pointer_entries(project_key, drop, tag_freq, keep, stamp, archive_rel=None, now=None):
    """给被移出的热 tag 生成「档案索引」指针条目（只指路，不编造结论）。"""
    out = []
    now_ms = int(now if now is not None else time.time() * 1000)
    have = {i.get("id") for i in keep}
    if not archive_rel:
        archive_rel = "memory/archive/projects/%s-%s.json" % (project_key, stamp)
    for tag, _n in tag_freq.most_common(MAX_POINTERS):
        dropped_tag = [i for i in drop if tag in (i.get("tags") or [])]
        if len(dropped_tag) < 5:
            continue
        kept_tag = [i for i in keep if tag in (i.get("tags") or [])]
        ids = ",".join(str(i.get("id")) for i in dropped_tag[:6])
        pid = "mem_arch_%s_%s" % (project_key[:8], re.sub(r"\W+", "", tag)[:12] or "x")
        if pid in have:
            continue
        out.append({
            "id": pid,
            "scope": "project",
            "type": "pattern",
            "content": (
                "【档案索引·%s】本项目 %d 条同类旧结论已于 %s 移入 %s"
                "（按 tag 过滤可取回原文）。主库现存同类 %d 条；被移走示例 id：%s。"
                % (tag, len(dropped_tag), stamp, archive_rel, len(kept_tag), ids)
            ),
            "tags": [tag, "档案索引"],
            "links": [],
            "origin": "manual",
            "status": "active",
            "pinned": False,
            "useCount": 0,
            "createdAt": now_ms,
            "updatedAt": now_ms,
            "lastUsedAt": 0,
            "sourceSessionId": None,
        })
    return out


def _backup(mem_dir, backup_dir):
    """按天备份整个 memory 目录；已存在则复用。返回备份路径。"""
    backup_dir = Path(backup_dir)
    if backup_dir.exists():
        return str(backup_dir)
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mem_dir, backup_dir, symlinks=True)
    return str(backup_dir)


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def archive(home, keep_n=DEFAULT_KEEP, apply=False, key=None, out=None, now=None, stamp=None):
    """分层归档：把低分条目移出主库并写档案 + 指针条目。

    默认 dry-run（apply=False）：只算方案，不改任何文件。
    apply=True 时先按天备份整个 memory 目录并打印路径，再逐个写档案/主库。
    """
    home = Path(home).expanduser()
    mem = home / "memory"
    proj = mem / "projects"
    arch = mem / "archive" / "projects"
    stamp = stamp or _dt.datetime.now().strftime("%Y%m%d-%H%M")
    now_ms = now if now is not None else time.time() * 1000

    targets = []
    if proj.is_dir():
        for f in sorted(proj.iterdir(), key=lambda p: p.name):
            if not (f.suffix == ".json" and f.is_file()):
                continue
            k = f.stem
            if key and k != key:
                continue
            _doc, items = load_store(f)
            if len(items) > OVER_ITEMS:
                targets.append((k, f, len(items)))

    result = {
        "stamp": stamp,
        "home": str(home),
        "keep_target": keep_n,
        "budget": BUDGET,
        "over_items": OVER_ITEMS,
        "applied": bool(apply),
        "targets": len(targets),
        "projects": [],
        "backups": [],
        "warnings": [],
    }
    if not targets:
        result["message"] = "没有超限项目（单文件 > %d 条），无需处理" % OVER_ITEMS
        return result

    backup_dir = home / "backup" / ("memory-" + stamp.split("-")[0])
    for k, path, count in targets:
        planned = plan(path, keep_n, now_ms)
        rel = "memory/archive/projects/%s-%s.json" % (k, stamp)
        pointers = pointer_entries(k, planned["drop"], planned["tag_freq"], planned["keep"], stamp,
                                   archive_rel=rel, now=now_ms)
        # 给指针留位置：总数不超过 OVER_ITEMS（必留本身超限时无法再裁，记 warning）。
        while pointers and len(planned["keep"]) + len(pointers) > OVER_ITEMS:
            pointers.pop()
        if len(planned["keep"]) > BUDGET:
            result["warnings"].append(
                "%s: 必留条目 %d > budget %d，规范性/置顶条目未被强行归档"
                % (k, len(planned["keep"]), BUDGET)
            )
        new_items = planned["keep"] + pointers
        apath = arch / ("%s-%s.json" % (k, stamp))
        row = {
            "key": k,
            "before": count,
            "keep": len(planned["keep"]),
            "pointers": len(pointers),
            "after": len(new_items),
            "moved": len(planned["drop"]),
            "archive": str(apath),
        }
        result["projects"].append(row)
        if not apply:
            continue
        bdir = _backup(mem, backup_dir)
        if bdir not in result["backups"]:
            result["backups"].append(bdir)
        arch_doc = {
            "version": 1,
            "kind": "memory-archive",
            "archived_at": stamp,
            "project_key": k,
            "keep_target": keep_n,
            "budget": BUDGET,
            "reason": "分层归档：低复用 / 时效性措辞 / 主题冗余条目移出主库",
            "count": len(planned["drop"]),
            "items": planned["drop"],
        }
        _write_json(apath, arch_doc)
        doc = planned["doc"]
        if not isinstance(doc, dict):
            doc = {}
        doc["items"] = new_items
        _write_json(path, doc)
        row["archive_sha256"] = hashlib.sha256(apath.read_bytes()).hexdigest()[:12]

    if out:
        out_dir = ensure_dir(out)
        plan_file = out_dir / ("archive-plan-%s.json" % stamp)
        _write_json(plan_file, result)
        result["plan_file"] = str(plan_file)
    return result
