"""命令行入口：ctxbudget <command>。

设计原则：
  * 默认只读 / dry-run；所有写操作都要显式 --apply（skills / memory-archive）；
  * 默认人类可读摘要，--json 输出机器可读结构；
  * 检出问题（例如能力地图里有失效路径）退出码 1，参数/输入错误 2。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, baseline as baseline_mod, capabilities, memory, paths, probe, skills


def _common_parser():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--dsh-home",
        default=None,
        metavar="DIR",
        help="agent 主目录（优先级：--dsh-home > $DSH_HOME > ~/.dsh）",
    )
    p.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return p


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ctxbudget",
        description="审计并压缩 agent 每轮请求的静态上下文（规则/记忆/技能/工具面/会话/KB）。默认只读。",
    )
    parser.add_argument("--version", action="version", version="ctxbudget " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>", title="子命令")

    def add(name, help_text):
        return sub.add_parser(name, parents=[_common_parser()], help=help_text)

    p = add("doctor", "检查主目录与依赖（只读）")
    p.add_argument("--workspace", default=None, help="同时检查该工作区的 AGENTS.md")

    p = add("capabilities", "校验 markdown 能力地图里的路径/命令")
    p.add_argument("--doc", default="CAPABILITIES.md", help="能力地图路径（默认 ./CAPABILITIES.md）")
    p.add_argument("--base", default=None, help="相对路径的解析基准（默认：当前目录）")

    p = add("skills", "压缩 SKILL.md 的 description（默认 dry-run）")
    p.add_argument("--root", default=None, help="技能目录（默认 <dsh-home>/skills）")
    p.add_argument("--max-len", type=int, default=skills.DEFAULT_MAX_LEN,
                   help="description 上限字符数（默认 180）")
    p.add_argument("--apply", action="store_true",
                   help="真正写盘（默认 dry-run）；写前生成 .bak-<stamp> 备份")

    p = add("memory-audit", "记忆库只读体检（重复/过期/超限）")
    p.add_argument("--out", default=None, metavar="DIR", help="把报告写成 markdown+json 到这个目录")

    p = add("memory-archive", "低分记忆分层归档（默认 dry-run）")
    p.add_argument("--apply", action="store_true", help="真正移动条目并写备份（默认 dry-run）")
    p.add_argument("--keep", type=int, default=memory.DEFAULT_KEEP,
                   help="每个项目目标保留条数（默认 %d）" % memory.DEFAULT_KEEP)
    p.add_argument("--key", default=None, help="只处理指定项目 key")
    p.add_argument("--out", default=None, metavar="DIR", help="把计划清单 JSON 写到这个目录")

    p = add("probe", "从会话日志推算工具面与静态上下文")
    p.add_argument("--since", default=None, help="只看最近一段时间，如 7d / 24h（缺省全部）")
    p.add_argument("--out", default=None, metavar="FILE", help="把结果 JSON 写到这个文件")

    p = add("baseline", "六层静态上下文基线快照（只读）")
    p.add_argument("--workspace", default=None, help="工作区目录（读取其 AGENTS.md）")
    p.add_argument("--kb", default=None, help="知识库目录或 kb.sqlite 路径（可选）")
    p.add_argument("--cost", action="store_true", help="附带读取 usage/成本层（若存在）")
    p.add_argument("--out", default=None, metavar="PATH", help="写 JSON 快照（目录或 .json 文件）")
    return parser


def _dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _fmt_bytes(value):
    return "-" if value is None else "%d B" % value


def cmd_doctor(args):
    home = paths.resolve_dsh_home(args.dsh_home)
    exists = home.is_dir()
    checks = [{"name": "dsh home", "path": str(home), "status": "ok" if exists else "missing"}]
    if exists:
        for label, rel in (("rules", "AGENTS.md"), ("memory", "memory"), ("skills", "skills"),
                           ("sessions", "sessions"), ("profiles", "profiles")):
            target = home / rel
            checks.append({"name": label, "path": str(target),
                           "status": "ok" if target.exists() else "absent"})
    zstd = shutil.which("zstdcat") or shutil.which("zstd")
    checks.append({
        "name": "zstd",
        "path": zstd or "",
        "status": "ok" if zstd else "warn",
        "note": "" if zstd else "未安装 zstdcat/zstd：probe 会跳过 .jsonl.zstd 会话",
    })
    if args.workspace:
        target = Path(args.workspace).expanduser() / "AGENTS.md"
        checks.append({"name": "workspace rules", "path": str(target),
                       "status": "ok" if target.is_file() else "absent"})
    if args.json:
        _dump({"home": str(home), "exists": exists, "checks": checks})
    else:
        print("ctxbudget %s doctor — 环境自检" % __version__)
        for c in checks:
            extra = ("  " + c["note"]) if c.get("note") else ""
            print("  %-15s %-4s %s%s" % (c["name"], c["status"], c["path"], extra))
        print()
        if not exists:
            print("找不到主目录：%s" % home)
            print("用 --dsh-home DIR 或环境变量 $DSH_HOME 指定；默认是 ~/.dsh。")
        else:
            print("提示：memory/skills 显示 absent 时，对应子命令会给出空结果而不是报错。")
    return 0 if exists else 1


def cmd_capabilities(args):
    doc = Path(args.doc).expanduser()
    if not doc.is_file():
        print("capabilities: 文件不存在: %s" % doc, file=sys.stderr)
        return 2
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print("capabilities: 读取失败: %s" % exc, file=sys.stderr)
        return 2
    base = Path(args.base).expanduser() if args.base else Path.cwd()
    result = capabilities.check(text, base)
    result.update({"doc": str(doc), "base": str(base)})
    if args.json:
        _dump(result)
    else:
        print("capabilities: %s（base=%s）" % (doc, base))
        print("checked ok=%d broken=%d" % (result["ok"], len(result["broken"])))
        for item in result["broken"]:
            print("  BROKEN %-7s %s" % (item["kind"], item["token"]))
            if item.get("path") and item["path"] != item["token"]:
                print("         -> %s" % item["path"])
    return 1 if result["broken"] else 0


def _tokens_estimate(chars, cn=False):
    return memory.estimate_tokens(chars, cn=cn)


def cmd_skills(args):
    home = paths.resolve_dsh_home(args.dsh_home)
    root = Path(args.root).expanduser() if args.root else home / "skills"
    if not root.is_dir():
        print("skills: 技能目录不存在: %s" % root, file=sys.stderr)
        print("用 --root DIR 或 --dsh-home 指定。", file=sys.stderr)
        return 2
    entries = skills.plan(root, args.max_len)
    applied = skills.apply(entries) if args.apply else None
    before = sum(e["old_len"] for e in entries if not e["error"])
    after = sum(e["new_len"] for e in entries if not e["error"])
    changed = [e for e in entries if e["changed"] and not e["error"]]
    errors = [e for e in entries if e["error"]]
    result = {
        "root": str(root),
        "max_len": args.max_len,
        "apply": bool(args.apply),
        "entries": entries,
        "applied": applied,
        "totals": {
            "skills": len(entries),
            "changed": len(changed),
            "errors": len(errors),
            "chars_before": before,
            "chars_after": after,
            "est_tokens_before": _tokens_estimate(before, cn=False),
            "est_tokens_after": _tokens_estimate(after, cn=False),
        },
    }
    if args.json:
        _dump(result)
    else:
        for entry in entries:
            if entry["error"]:
                print("%-22s SKIP %s" % (entry["name"], entry["error"]))
            else:
                mark = "->" if entry["changed"] else "=="
                print("%-22s %4d %s %4d" % (entry["name"], entry["old_len"], mark, entry["new_len"]))
        print("---")
        print("chars: %d -> %d（估算 tokens@EN %.1f: %d -> %d）changed=%d errors=%d"
              % (before, after, memory.EN_CHARS_PER_TOKEN,
                 _tokens_estimate(before, cn=False), _tokens_estimate(after, cn=False),
                 len(changed), len(errors)))
        if not args.apply:
            print("dry-run：未写盘。加 --apply 才会修改，并生成 <file>.bak-<stamp> 备份。")
        else:
            for bak in applied["backups"]:
                print("备份 -> %s" % bak)
            print("已写 %d 个文件。" % len(applied["written"]))
            for err in applied["errors"]:
                print("  写失败 %s: %s" % (err["name"], err["error"]))
    if args.apply and applied and applied["errors"]:
        return 1
    return 0


def _audit_markdown(result):
    lines = [
        "# 记忆库体检 · " + result["generated_at"],
        "",
        "- home: " + result["home"],
        "- 总条目：**%d**（文件 %d 个）" % (result["total"], result["files"]),
        "- 无 tags：**%d**" % result["no_tags"],
        "- 完全相同内容重复组（含跨项目）：**%d**" % result["dup_group_count"],
        "- 超过 %d 天：**%d**" % (memory.STALE_DAYS, len(result["stale"])),
        "- 超限文件（>%d 条或 >%d tokens 估算）：**%d**"
        % (memory.OVER_ITEMS, memory.OVER_TOKENS, len(result["over"])),
        "- 字段口径：" + result["schema_note"],
        "",
        "## 各文件分布（Top 15）",
        "",
        "| scope | items | chars | est tokens(CN) |",
        "| --- | --- | --- | --- |",
    ]
    for row in result["rows"][:15]:
        lines.append("| %s | %d | %d | %d |"
                     % (row["scope"], row["items"], row["chars"], row["est_tokens_cn"]))
    if result["over"]:
        lines += ["", "## 超限文件", ""]
        for row in result["over"]:
            lines.append("- %s: %d 条 / ~%d tokens"
                         % (row["scope"], row["items"], row["est_tokens_cn"]))
    if result["duplicates"]:
        lines += ["", "## 重复组（前 10）", ""]
        for key, members in list(result["duplicates"].items())[:10]:
            detail = ", ".join("%s@%s" % (m.get("id") or "?", m.get("scope")) for m in members[:4])
            lines.append("- %s x%d: %s" % (key, len(members), detail))
    if result["stale"]:
        lines += ["", "## 超龄条目（前 10）", ""]
        for item in result["stale"][:10]:
            lines.append("- %s @%s (%dd)" % (item["id"], item["scope"], item["age_days"]))
    lines += ["", "> 本报告只读；未修改任何记忆条目。"]
    return "\n".join(lines) + "\n"


def cmd_memory_audit(args):
    home = paths.resolve_dsh_home(args.dsh_home)
    result = memory.audit(home)
    if args.out:
        out_dir = paths.ensure_dir(args.out)
        ts = paths.stamp()
        md_path = out_dir / ("memory-audit-%s.md" % ts)
        json_path = out_dir / ("memory-audit-%s.json" % ts)
        md_path.write_text(_audit_markdown(result), encoding="utf-8")
        _write_json(json_path, result)
        result["out_files"] = [str(md_path), str(json_path)]
    if args.json:
        _dump(result)
        return 0
    print("记忆库体检（只读）")
    print("home: %s" % result["home"])
    if not (home / "memory").is_dir():
        print("注意：没有找到 memory/ 目录，以下数字都是 0。")
    print("总条目：%d（文件 %d 个）" % (result["total"], result["files"]))
    print("无 tags：%d" % result["no_tags"])
    print("重复组：%d" % result["dup_group_count"])
    print("超 %d 天：%d" % (memory.STALE_DAYS, len(result["stale"])))
    print("超限文件：%d" % len(result["over"]))
    print()
    print("各文件分布（Top 10）：")
    for row in result["rows"][:10]:
        print("  %-26s %5d 条 %9d 字符 ≈ %6d tokens"
              % (row["scope"], row["items"], row["chars"], row["est_tokens_cn"]))
    if result["over"]:
        print()
        print("超限文件（>%d 条或 >%d tokens 估算）：" % (memory.OVER_ITEMS, memory.OVER_TOKENS))
        for row in result["over"]:
            print("  %s: %d 条 / ~%d tokens" % (row["scope"], row["items"], row["est_tokens_cn"]))
    if result.get("out_files"):
        print()
        for path in result["out_files"]:
            print("报告 -> %s" % path)
    return 0


def cmd_memory_archive(args):
    if args.keep < 1:
        print("memory-archive: --keep 必须 >= 1", file=sys.stderr)
        return 2
    home = paths.resolve_dsh_home(args.dsh_home)
    result = memory.archive(home, keep_n=args.keep, apply=args.apply, key=args.key, out=args.out)
    if args.json:
        _dump(result)
        return 0
    mode = "apply（会写盘）" if args.apply else "dry-run（默认，不写盘）"
    print("memory-archive — %s" % mode)
    print("home=%s  keep_target=%d  budget=%d  over=%d"
          % (home, args.keep, memory.BUDGET, memory.OVER_ITEMS))
    if result.get("message"):
        print(result["message"])
        return 0
    for row in result["projects"]:
        print("%-18s %5d -> %4d 留 + %2d 指针 = %4d（移出 %d）"
              % (row["key"], row["before"], row["keep"], row["pointers"],
                 row["after"], row["moved"]))
    for warning in result.get("warnings") or []:
        print("警告：" + warning)
    if result["applied"]:
        for bdir in result["backups"]:
            print("备份 -> %s" % bdir)
        for row in result["projects"]:
            print("档案 -> %s（sha256 %s）" % (row["archive"], row.get("archive_sha256", "?")))
    elif result["targets"]:
        print("dry-run：未修改任何文件。加 --apply 才会移动条目（写前按天备份整个 memory 目录）。")
    if result.get("plan_file"):
        print("清单 -> %s" % result["plan_file"])
    return 0


def cmd_probe(args):
    home = paths.resolve_dsh_home(args.dsh_home)
    try:
        result = probe.scan(home, args.since)
    except ValueError as exc:
        print("probe: %s" % exc, file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["out"] = str(out)
    if args.json:
        _dump(result)
        return 0
    print("扫描会话 %d 个，冷启动可算样本 %d 个"
          % (result["sessions_scanned"], result["clean_samples"]))
    if not (home / "sessions").is_dir():
        print("注意：没有找到 sessions/ 目录。")
    skipped = result["skipped"]
    if skipped["zstd_unavailable"]:
        print("注意：缺少 zstdcat/zstd，跳过 %d 个 .jsonl.zstd 会话" % skipped["zstd_unavailable"])
    if skipped["read_errors"]:
        print("注意：%d 个日志读取失败，已跳过" % skipped["read_errors"])
    surface = result["latest_tools_surface"]
    if surface:
        print("最新工具面：%d 个工具 / %d 字符 ≈ %s tokens（日志下界）"
              % (surface["tools_count"], surface["tools_chars"], surface["tools_tokens_est"]))
    if result["last_clean"]:
        print()
        print("最近冷启动样本（静态上下文推算）：")
        for rec in result["last_clean"]:
            print("  %s  prompt1=%6d  static≈%s"
                  % (rec["mtime"], rec["prompt1"], rec["static_est"]))
    else:
        print()
        print("还没有可用的冷启动样本（需要一个 cacheRead=0 的新会话）；下次跑会自动抓到。")
    print()
    print("限制：" + result["note"])
    if result.get("out"):
        print("结果 -> %s" % result["out"])
    return 0


def _print_baseline(snap):
    l1 = snap["L1_rules"]
    l2 = snap["L2_memory"]
    l3 = snap["L3_skills"]
    l4 = snap["L4_tools"]
    l5 = snap["L5_sessions"]
    l6 = snap["L6_kb"]
    cordis = l4.get("cordis") or {}
    print("ctxbudget baseline — 六层静态上下文快照")
    print("home=%s  workspace=%s" % (snap["home"], snap["workspace"]))
    print("  L1 rules    global=%s  project=%s"
          % (_fmt_bytes(l1.get("global_agents_bytes")), _fmt_bytes(l1.get("project_agents_bytes"))))
    print("  L2 memory   %d 条 / %d 字符 ≈ %d tokens（CN 估算）"
          % (l2["items"], l2["chars"], l2["est_tokens_cn"]))
    print("  L3 skills   %d 个 / %d 字符 ≈ %d tokens（EN 估算）"
          % (l3["count"], l3["metadata_chars"], l3["est_tokens"]))
    print("  L4 tools    cordis entries=%d disabled=%d mcp_enabled=%s；schema tokens=%s"
          % (cordis.get("entries", 0), cordis.get("disabled", 0),
             cordis.get("mcp_enabled", "-"), l4.get("tool_schema_tokens")))
    print("  L5 sessions %d 个文件 / %s MB" % (l5["files"], l5["mb"]))
    if l6.get("db"):
        print("  L6 kb       %s  pages=%s chunks=%s"
              % (l6["db"], l6.get("pages", "-"), l6.get("chunks", "-")))
    else:
        print("  L6 kb       %s" % l6.get("note"))
    if "X2_cost" in snap:
        cost = snap["X2_cost"]
        print("  X2 cost     balance=%s today=%s min_turn_tokens=%s"
              % (cost.get("balance"), cost.get("today"), cost.get("min_turn_tokens")))
    print()
    print("注：字符/token 为估算（中文 %.0f、英文 %.1f 字符/token）；工具 schema 与 system prompt "
          "无法从日志完整测出，见 L4 note。" % (memory.CN_CHARS_PER_TOKEN, memory.EN_CHARS_PER_TOKEN))


def cmd_baseline(args):
    home = paths.resolve_dsh_home(args.dsh_home)
    if not home.is_dir():
        print("baseline: 主目录不存在: %s" % home, file=sys.stderr)
        print("用 --dsh-home DIR 或 $DSH_HOME 指定。", file=sys.stderr)
        return 2
    snap = baseline_mod.snapshot(home, workspace=args.workspace, kb=args.kb, cost=args.cost)
    if args.out:
        out = Path(args.out).expanduser()
        if out.suffix.lower() == ".json":
            target = _write_json(out, snap)
        else:
            target = _write_json(out / ("baseline-%s.json" % paths.stamp()), snap)
        snap["out"] = str(target)
    if args.json:
        _dump(snap)
        return 0
    _print_baseline(snap)
    if snap.get("out"):
        print("快照 -> %s" % snap["out"])
    return 0


HANDLERS = {
    "doctor": cmd_doctor,
    "capabilities": cmd_capabilities,
    "skills": cmd_skills,
    "memory-audit": cmd_memory_audit,
    "memory-archive": cmd_memory_archive,
    "probe": cmd_probe,
    "baseline": cmd_baseline,
}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return HANDLERS[args.command](args)
    except BrokenPipeError:
        return 0
