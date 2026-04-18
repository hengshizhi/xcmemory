"""
星尘记忆系统 (xcmemory_interest)
StarDust Memory - Structured Memory Retrieval System:

模块结构：
├── vector_db/         # 向量数据库封装 ✅
├── embedding_coder/  # 查询句嵌入编码（InterestEncoder + QueryEncoder）
├── lifecycle_manager/ # 生命周期决定和更新
├── basic_crud/       # 向量数据库 CRUD（VecDBCRUD + BasicCRUD）
├── auxiliary_query/  # 辅助查询（时间索引 + 槽位索引）
├── mql/              # Memory Query Language（MQL）✅
├── pyapi            # Python 应用层封装（组合所有模块）
├── graph_query/      # 图查询（隐式图 + 多跳查询）
├── version_control/  # 单记忆版本控制 ✅
├── online_learning/  # 在线学习和数据集管理
└── api/              # HTTP/WS API
"""

__version__ = "0.4.0"

# 导出主要接口
from .vector_db import (
    ChromaVectorDB,
    SubspaceSearcher,
    HybridSearcher,
    SubspaceReranker,
    ResultConditioningReranker,
    DynamicReranker,
)
from .embedding_coder import InterestEncoder, QueryEncoder
from .basic_crud import VecDBCRUD, BasicCRUD
from .pyapi import PyAPI, MQLInterpreter, MQLResult
from .lifecycle_manager import (
    LifecycleManager,
    ProbabilitySampler,
    LIFECYCLE_INFINITY,
)
from .version_control import (
    VersionManager,
    MemoryVersion,
    VersionDiff,
    ChangeType,
)
from .mql import (
    Interpreter,
    QueryResult,
    parse,
    MQLError,
    ParseError,
)

__all__ = [
    # CRUD
    "VecDBCRUD",
    "BasicCRUD",
    # Vector DB
    "ChromaVectorDB",
    "SubspaceSearcher",
    "HybridSearcher",
    "SubspaceReranker",
    "ResultConditioningReranker",
    "DynamicReranker",
    # Embedding
    "InterestEncoder",
    "QueryEncoder",
    # PyAPI
    "PyAPI",
    "MQLInterpreter",
    "MQLResult",
    # Lifecycle
    "LifecycleManager",
    "ProbabilitySampler",
    "LIFECYCLE_INFINITY",
    # Version Control
    "VersionManager",
    "MemoryVersion",
    "VersionDiff",
    "ChangeType",
    # MQL
    "Interpreter",
    "QueryResult",
    "parse",
    "MQLError",
    "ParseError",
]