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

# ============================================================================
# 延迟导入（所有模块都延迟加载，避免 torch DLL 问题时阻塞整个包）
# ============================================================================

def __getattr__(name):
    """延迟导入所有模块，避免顶层加载 torch"""
    _lazy_imports = {
        # Embedding（torch 依赖）
        "InterestEncoder": (".embedding_coder", "InterestEncoder"),
        "QueryEncoder": (".embedding_coder", "QueryEncoder"),
        # CRUD（torch 依赖）
        "VecDBCRUD": (".basic_crud", "VecDBCRUD"),
        "BasicCRUD": (".basic_crud", "BasicCRUD"),
        # Vector DB（chromadb，间接 torch 依赖）
        "ChromaVectorDB": (".vector_db", "ChromaVectorDB"),
        "SubspaceSearcher": (".vector_db", "SubspaceSearcher"),
        "HybridSearcher": (".vector_db", "HybridSearcher"),
        "SubspaceReranker": (".vector_db", "SubspaceReranker"),
        "ResultConditioningReranker": (".vector_db", "ResultConditioningReranker"),
        "DynamicReranker": (".vector_db", "DynamicReranker"),
        # PyAPI（torch 依赖）
        "PyAPI": (".pyapi", "PyAPI"),
        "MQLInterpreter": (".pyapi", "MQLInterpreter"),
        "MQLResult": (".pyapi", "MQLResult"),
        # Lifecycle
        "LifecycleManager": (".lifecycle_manager", "LifecycleManager"),
        "ProbabilitySampler": (".lifecycle_manager", "ProbabilitySampler"),
        "LIFECYCLE_INFINITY": (".lifecycle_manager", "LIFECYCLE_INFINITY"),
        # Version Control
        "VersionManager": (".version_control", "VersionManager"),
        "MemoryVersion": (".version_control", "MemoryVersion"),
        "VersionDiff": (".version_control", "VersionDiff"),
        "ChangeType": (".version_control", "ChangeType"),
        # MQL
        "Interpreter": (".mql", "Interpreter"),
        "QueryResult": (".mql", "QueryResult"),
        "parse": (".mql", "parse"),
        "MQLError": (".mql", "MQLError"),
        "ParseError": (".mql", "ParseError"),
    }

    if name in _lazy_imports:
        module_path, attr_name = _lazy_imports[name]
        from . import importlib
        module = importlib.import_module(module_path, __package__)
        return getattr(module, attr_name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
