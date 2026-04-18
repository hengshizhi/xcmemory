# 星尘记忆系统 - 开发文档

> 星尘记忆系统 (xcmemory_interest) v0.4.0

## 目录

1. [项目结构](#1-项目结构)
2. [模块设计](#2-模块设计)
3. [核心组件](#3-核心组件)
4. [数据流](#4-数据流)
5. [扩展指南](#5-扩展指南)
6. [测试](#6-测试)
7. [性能优化](#7-性能优化)

---

## 1. 项目结构

```
models/xcmemory_interest/
├── __init__.py              # 模块入口，导出主要接口
├── config.py                # 配置参数
├── user_manager.py          # 用户鉴权核心
│
├── basic_crud/              # 向量数据库 CRUD
│   ├── __init__.py
│   ├── vec_db_crud.py      # VecDBCRUD 主类
│   ├── basic_crud.py       # 基础 CRUD（旧版）
│   └── DESIGN.md
│
├── vector_db/               # 向量数据库封装
│   ├── __init__.py
│   ├── chroma_vector_db.py # Chroma 封装
│   ├── subspace_search.py  # 子空间搜索
│   ├── reranker.py         # 重排序
│   └── DESIGN.md
│
├── embedding_coder/         # 查询句嵌入编码
│   ├── __init__.py
│   ├── model.py            # InterestEncoder 模型
│   ├── query_encoder.py   # 查询编码管道
│   └── DESIGN.md
│
├── auxiliary_query/         # 辅助查询
│   ├── __init__.py
│   ├── scheduler.py        # 数据库调度器
│   ├── storage/
│   │   ├── kv_db.py       # KV 数据库（LMDB）
│   │   └── sql_db.py      # SQL 数据库（SQLite）
│   ├── interpreter/
│   │   ├── parser.py      # DSL 语法解析器
│   │   └── core.py        # DSL 解释器
│   ├── indexes/
│   │   ├── time_index.py  # 时间索引
│   │   └── slot_index.py  # 槽位索引
│   └── DESIGN.md
│
├── lifecycle_manager/       # 生命周期管理
│   ├── __init__.py
│   ├── core.py            # 生命周期核心
│   └── DESIGN.md
│
├── mql/                     # MQL 查询语言
│   ├── __init__.py
│   ├── lexer.py           # 词法分析器
│   ├── parser.py          # 语法分析器
│   ├── interpreter.py     # 解释器
│   ├── interpreter_extended.py  # 扩展解释器
│   ├── errors.py          # 错误定义
│   └── DESIGN.md
│
├── pyapi/                   # Python 应用层封装
│   ├── __init__.py
│   ├── core.py            # PyAPI + MemorySystem
│   ├── examples.py        # 示例
│   └── test_pyapi.py
│
├── netapi/                  # HTTP/WS API
│   ├── __init__.py
│   └── DESIGN.md
│
├── version_control/         # 版本控制
│   ├── __init__.py
│   ├── version_manager.py # 版本管理器
│   ├── models.py          # 版本数据模型
│   └── DESIGN.md
│
├── graph_query/             # 图查询
│   ├── __init__.py
│   ├── graph.py           # 图结构
│   ├── explorer.py        # 图探索器
│   └── DESIGN.md
│
├── online_learning/          # 在线学习
│   ├── __init__.py
│   └── DESIGN.md
│
└── docs/                    # 文档
    ├── USER_GUIDE.md      # 使用文档
    ├── DEVELOPER_GUIDE.md # 本文档
    └── API_REFERENCE.md   # API 参考
```

---

## 2. 模块设计

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    netapi (HTTP/WS)                      │
│                  APIServer + WebSocket                   │
├─────────────────────────────────────────────────────────┤
│                      pyapi                               │
│              PyAPI + MemorySystem                        │
├─────────────────────────────────────────────────────────┤
│    mql/          │  version_control/  │  user_manager/   │
│  MQL 解释器      │   版本管理器        │   用户鉴权       │
├─────────────────────────────────────────────────────────┤
│                     basic_crud                           │
│                    VecDBCRUD                             │
├─────────────────────────────────────────────────────────┤
│   vector_db/     │  auxiliary_query/  │ lifecycle_mgr/  │
│  Chroma 封装    │   索引 + 调度       │  生命周期       │
├─────────────────────────────────────────────────────────┤
│                  embedding_coder                          │
│               InterestEncoder (暂不支持)                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| `embedding_coder` | 查询句嵌入编码 | 独立 |
| `vector_db` | Chroma 向量数据库封装 | Chroma |
| `basic_crud` | 记忆 CRUD 操作 | vector_db, auxiliary_query |
| `auxiliary_query` | 时间/槽位索引、SQL/KV 调度 | SQLite, LMDB |
| `lifecycle_manager` | 生命周期计算和更新 | auxiliary_query |
| `version_control` | 记忆版本管理 | basic_crud |
| `mql` | MQL 解析和执行 | basic_crud, version_control |
| `pyapi` | 统一 Python API | 以上所有 |
| `netapi` | HTTP/WS 网络接口 | pyapi, mql, user_manager |
| `user_manager` | 用户认证和权限 | SQLite |

---

## 3. 核心组件

### 3.1 VecDBCRUD

向量数据库 CRUD 主类，管理记忆的增删查改。

```python
class VecDBCRUD:
    def __init__(
        self,
        persist_directory: str,
        vocab_size: int = 32000,
        slot_dim: int = 64,
        enable_interest_mode: bool = False,
        similarity_threshold: float = 0.5,
    ):
        """初始化"""

    def create_collections(self):
        """创建所有 collection"""

    def insert(
        self,
        query_sentence: str,
        content: str,
        lifecycle: int,
        query_embedding: np.ndarray,
        raw_embedding: np.ndarray,
    ) -> str:
        """插入记忆，返回 memory_id"""

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        filter_conditions: dict = None,
    ) -> List[SearchResult]:
        """向量搜索"""

    def get(self, memory_id: str) -> Optional[Memory]:
        """获取单条记忆"""

    def update(self, memory_id: str, **kwargs) -> bool:
        """更新记忆"""

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""

    def list_by_filter(
        self,
        conditions: List[Condition],
        limit: int = 100,
    ) -> List[Memory]:
        """条件列表"""
```

### 3.2 MQL 解释器

MQL → AST → 执行结果

```python
class Interpreter:
    def bind(self, name: str, obj: Any):
        """绑定对象（mem, api, um）"""

    def set_auth_context(self, auth: AuthContext):
        """设置认证上下文"""

    def execute(self, mql: str) -> MQLResult:
        """执行单条 MQL"""

    def execute_script(self, script: str) -> List[MQLResult]:
        """执行多行 MQL（分号分隔）"""
```

### 3.3 PyAPI

统一 Python API，组合所有模块。

```python
class PyAPI:
    def __init__(self, database_root: str):
        """初始化，database_root 下管理多个记忆系统"""

    def create_system(
        self,
        name: str,
        enable_interest_mode: bool = False,  # 当前版本不支持 True
        similarity_threshold: float = 0.5,
    ) -> MemorySystem:
        """创建记忆系统"""

    def get_system(self, name: str) -> MemorySystem:
        """获取记忆系统"""

    def list_systems(self) -> List[str]:
        """列出所有系统"""

    def set_active_system(self, name: str):
        """切换活跃系统"""

    def delete_system(self, name: str):
        """删除系统"""

    def execute(self, mql: str) -> MQLResult:
        """在活跃系统上执行 MQL"""
```

### 3.4 MemorySystem

单个记忆系统的操作接口。

```python
class MemorySystem:
    name: str
    crud: VecDBCRUD
    version_manager: VersionManager
    lifecycle_manager: LifecycleManager
    scheduler: Scheduler

    def execute(self, mql: str) -> MQLResult:
        """执行 MQL"""

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
    ) -> List[Memory]:
        """向量搜索"""

    def insert(
        self,
        query_sentence: str,
        content: str,
        lifecycle: int,
    ) -> str:
        """插入记忆"""
```

---

## 4. 数据流

### 4.1 记忆插入流程

```
用户输入
    ↓
query_sentence: "<平时><我><学><编程><喜欢><有收获>"
content: "我喜欢学编程"
lifecycle: 86400
    ↓
┌─────────────────────────────────┐
│ InterestEncoder (当前不支持)      │
│ → query_embedding [384]         │
│ → raw_embedding [384]           │
└─────────────────────────────────┘
    ↓
VecDBCRUD.insert()
    ↓
    ├── Chroma: 存储向量
    │   ├── slot_* collections (64维)
    │   └── full_vectors collection (384维)
    │
    ├── SQLite: 存储 metadata
    │   └── memories 表
    │
    ├── LMDB: 存储 Memory 对象
    │   └── memory_id → Memory
    │
    └── TimeIndex: 时间索引
        └── created_at → memory_ids
```

### 4.2 记忆查询流程

```
用户查询
    ↓
MQL: "SELECT * FROM memories WHERE [subject='我'] SEARCH TOPK 5"
    ↓
┌─────────────────────────────────┐
│ MQL 解释器                       │
│ 1. parse() → AST                │
│ 2. execute() → MQLResult        │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│ 子空间搜索                       │
│ 1. 查询各 slot_* collection     │
│ 2. 取交集/并集                  │
│ 3. 计算 match_count             │
│ 4. 排序: (match_count↓, dist↑) │
└─────────────────────────────────┘
    ↓
┌─────────────────────────────────┐
│ 获取完整记录                      │
│ 1. 查 full_vectors collection   │
│ 2. 查 SQLite memories 表         │
│ 3. 查 LMDB Memory 对象          │
└─────────────────────────────────┘
    ↓
返回 MQLResult
```

---

## 5. 扩展指南

### 5.1 添加新的 MQL 语句

1. 在 `mql/parser.py` 添加语法规则
2. 在 `mql/interpreter.py` 添加执行逻辑
3. 在 `mql/errors.py` 添加错误类型
4. 添加单元测试

```python
# 示例：添加 SUMMARIZE 语句
# mql/parser.py
def parse_summarize(self):
    self.expect("SUMMARIZE")
    # 解析 SUMMARIZE 语句
    return SummarizeNode(...)

# mql/interpreter.py
def execute_summarize(self, node):
    # 执行逻辑
    result = self.mem.summarize()
    return MQLResult(...)
```

### 5.2 添加新的索引类型

在 `auxiliary_query/indexes/` 添加索引类：

```python
# auxiliary_query/indexes/my_index.py
class MyIndex:
    def __init__(self, db_path: str):
        self.db = ...

    def add(self, memory_id: str, value: Any):
        ...

    def query(self, value: Any) -> List[str]:
        ...
```

在 `Scheduler` 中注册：

```python
# auxiliary_query/scheduler.py
class Scheduler:
    def __init__(self):
        self.time_index = TimeIndex(...)
        self.slot_index = SlotIndex(...)
        self.my_index = MyIndex(...)  # 新增

    def register_index(self, index_name: str, index):
        ...
```

### 5.3 添加新的向量搜索策略

在 `vector_db/subspace_search.py` 添加：

```python
class NewSearcher:
    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        **kwargs,
    ) -> List[SearchResult]:
        # 新策略实现
        ...
```

---

## 6. 测试

### 6.1 运行测试

```bash
# 运行所有测试
o:/project/starlate/venv/Scripts/python.exe -m pytest tests/ -v

# 运行特定模块测试
o:/project/starlate/venv/Scripts/python.exe -m pytest tests/test_mql.py -v

# 运行示例脚本
o:/project/starlate/venv/Scripts/python.exe demo_mql_auth.py
```

### 6.2 编写测试

```python
# tests/test_my_feature.py
import pytest
from xcmemory_interest import PyAPI

@pytest.fixture
def api():
    api = PyAPI("./test_data/test_api")
    yield api
    api.delete_system("test")

def test_create_system(api):
    system = api.create_system("test")
    assert system.name == "test"
```

---

## 7. 性能优化

### 7.1 批量操作

```python
# 批量插入
batch = [
    ("<平时><我><学><编程>", "学编程", 86400),
    ("<经常><我><看><书>", "看书", 86400),
    ("<偶尔><我><玩><游戏>", "玩游戏", 3600),
]
for query, content, lifecycle in batch:
    system.insert(query, content, lifecycle)
```

### 7.2 向量缓存

```python
# 避免重复计算查询向量
cache = {}

def get_query_embedding(query_sentence):
    if query_sentence not in cache:
        cache[query_sentence] = encoder.encode(query_sentence)
    return cache[query_sentence]
```

### 7.3 Chroma 批处理

```python
# VecDBCRUD 使用 Chroma 的 batch 操作
# 内部已优化：累积后批量 add
```
