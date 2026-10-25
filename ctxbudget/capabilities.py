"""能力地图校验：解析 markdown 里的反引号 token，检查路径/命令是否还在。

只做存在性判断，绝不执行 token 指向的东西。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# 行内反引号 token，长度 2..160（与生产脚本一致）。
# 用 \x60 表示反引号，避免源码里出现裸反引号造成转义/复制粘贴歧义。
TOKEN_RE = re.compile(r"\x60([^\x60]{2,160})\x60")

# 相对路径：至少一段目录，例如 docs/README.md、tools/run.sh（不含空格/通配符）
_REL_PATH_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?$")
# 单段命令名：例如 python3、rg
_CMD_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]*$")
# token 末尾常见的行文标点，不属于路径本身
_TRAILING = ".,;:)]}\u3002\uff0c\uff1b\uff1a\uff09\u3011\u3001"


def parse_tokens(text):
    """抽出行内反引号 token（去重、排序，输出确定）。"""
    tokens = set(TOKEN_RE.findall(str(text or "")))
    return sorted(tokens)


def _strip_token(token):
    """取 token 里的路径部分：丢掉参数与行文标点。"""
    raw = str(token).strip()
    if not raw:
        return ""
    head = raw.split()[0]
    return head.rstrip(_TRAILING)


def check(text, base=None):
    """校验一段 markdown 里的路径/命令。

    返回 {"ok": int, "broken": [{"kind", "token", "path"}], "checked": int}

    判定规则（与生产版一致）：
      * 绝对路径 token（以 / 开头、含 /、不是通配符）→ 必须存在；
      * 相对路径 token（形如 a/b/c）→ 相对 base 解析后必须存在；
      * 单段命令名 → shutil.which 找到就记一次 ok；找不到**不计 broken**
        （文档里常把普通英文单词包进反引号，硬判会造成大量误报）；
      * 通配符 token 跳过（静态判定不了）。
    """
    base_path = Path(base).expanduser() if base else None
    ok = 0
    broken = []
    for token in parse_tokens(text):
        target = _strip_token(token)
        if not target:
            continue
        if target.endswith("*"):
            continue
        if token.strip().startswith("/"):
            if "/" in target:
                if os.path.exists(target):
                    ok += 1
                else:
                    broken.append({"kind": "path", "token": token, "path": target})
            continue
        if _REL_PATH_RE.match(target):
            path = str(base_path / target) if base_path else target
            if os.path.exists(path):
                ok += 1
            else:
                broken.append({"kind": "relpath", "token": token, "path": path})
            continue
        if _CMD_RE.match(target) and shutil.which(target):
            ok += 1
    return {"ok": ok, "broken": broken, "checked": ok + len(broken)}
