"""
星尘记忆 - Python 应用层 API

整合所有模块，提供统一的 Python 接口给应用层。

模块架构：
├── basic_crud / VecDBCRUD   # 向量存储（写入、删除）
├── auxiliary_query          # 调度器、索引
│   ├── scheduler/           # 数据库生命周期管理
│   ├── indexes/            # TimeIndex、SlotIndex
│   └── interpreter/         # DSL 解释器
├── mql                      # Memory Query Language（MQL 解释器）
└── pyapi                   # 应用层封装（组合所有模块）

核心概念：
- MemorySystem: 单个独立的记忆系统，包含完整数据库
- PyAPI: 管理多个 MemorySystem，提供统一访问接口

使用方式：
1. 创建 PyAPI 实例
2. create_system() 创建新的记忆系统
3. 系统.write() / 系统.search() 操作单个记忆系统
4. 系统.execute() 使用 MQL 查询语言
5. 系统.get_memory() 获取记忆内容
"""

from .core import PyAPI, MemorySystem, SearchResult, EmbeddingMode, LifecycleQueryResult
from .core import MQLInterpreter, MQLResult

__all__ = [
    "PyAPI",
    "MemorySystem",
    "SearchResult",
    "EmbeddingMode",
    "LifecycleQueryResult",
    # MQL
    "MQLInterpreter",
    "MQLResult",
]