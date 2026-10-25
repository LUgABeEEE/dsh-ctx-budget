"""路径解析。

不同机器上的 agent 主目录布局各不相同，所以这里不做任何「猜路径」：
优先级固定为  --dsh-home  >  $DSH_HOME  >  ~/.dsh
输出目录同理只认显式 --out（或调用方给的默认值），源码里不写死任何本机路径。
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

DEFAULT_DIRNAME = ".dsh"


def resolve_dsh_home(cli_value=None, env=None):
    """返回 agent 主目录（不校验存在性）。

    env 参数用于测试注入；缺省读 os.environ。
    """
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ if env is None else env
    value = env.get("DSH_HOME")
    if value and value.strip():
        return Path(value).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def resolve_out(cli_value=None, default=None):
    """返回输出路径：显式 --out 优先，否则用 default。

    default 为 None 表示「只打印、不落盘」。不会自动创建目录。
    """
    if cli_value:
        return Path(cli_value).expanduser()
    return Path(default).expanduser() if default else None


def stamp(fmt="%Y%m%d-%H%M%S", now=None):
    """时间戳字符串；now 可注入以便测试。"""
    now = now or _dt.datetime.now()
    return now.strftime(fmt)


def ensure_dir(path):
    """mkdir -p 并返回 Path。"""
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def as_path(value):
    """None 透传，其它转 Path。"""
    return Path(value).expanduser() if value is not None else None
