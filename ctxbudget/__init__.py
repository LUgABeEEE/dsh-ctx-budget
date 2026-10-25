"""dsh-ctx-budget — 审计并压缩 agent 每轮请求的静态上下文。

六个层：规则 / 记忆 / 技能 / 工具面 / 会话 / 知识库。
默认只读；所有写操作都需要显式 --apply，且写前自动备份。
"""
from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
