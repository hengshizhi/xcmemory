"""
MemoryGraph - 记忆图查询模块

基于槽位值索引构建记忆关联图，支持图搜索。

概念：
- 节点（Node）：记忆（memory_id）
- 边（Edge）：两个记忆在某个槽位有相同的值

例如：
  记忆A: <平时><我><学习><Python><提升><成长>
  记忆B: <最近><我><学习><Go><掌握><并发>
  → A 和 B 通过 subject=我, action=学习 相连
"""

from .graph import MemoryGraph, GraphSearchResult
from .explorer import GraphExplorer

__all__ = ["MemoryGraph", "GraphSearchResult", "GraphExplorer"]