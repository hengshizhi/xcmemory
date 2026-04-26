# 星尘记忆系统 - 辅助查询模块

> 管理人：XC Memory Team
> 状态：设计完成

## 概述

本模块是「星尘记忆系统」的辅助查询层，提供四大核心能力：

1. **存储引擎抽象**：统一的 KV 数据库和 SQL 数据库接口
2. **解释器**：绑定运行时对象，通过 DSL 语法执行方法调用
3. **调度器**：管理多个数据库实例的生命周期
4. **应用索引**：TimeIndex（时间索引）和 SlotIndex（槽位索引）

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Auxiliary Query Layer                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  Interpreter │  │ Scheduler   │  │    Application Index    │  │
│  │             │  │             │  │  ┌─────────┐ ┌───────┐  │  │
│  │ • bind()    │  │ • create()  │  │  │TimeIndex│ │SlotIdx│  │  │
│  │ • eval()    │  │ • get()     │  │  └─────────┘ └───────┘  │  │
│  │ • execute() │  │ • delete()  │  └─────────────────────────┘  │
│  └─────────────┘  └─────────────┘                               │
│         │                │                                       │
│         ▼                ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   Storage Engine Interface                   ││
│  │  ┌──────────────────┐    ┌──────────────────────────────┐  ││
│  │  │   KVDatabase     │    │      SQLDatabase             │  ││
│  │  │   (LMDB)         │    │      (SQLite)                │  ││
│  │  │  • get/set/del   │    │  • execute/select/insert     │  ││
│  │  │  • batch ops     │    │  • transaction support       │  ││
│  │  │  • TTL support   │    │                              │  ││
│  │  └──────────────────┘    └──────────────────────────────┘  ││
│  └─────────────────────────────────────────────────────────────┘│
│                            │                                     │
│                            ▼                                     │
│                   ┌─────────────────┐                            │
│                   │  FileSystem     │                            │
│                   │  ./data/aux_db/ │                            │
│                   │  • kv_store/   │                            │
│                   │  • sql_store/  │                            │
│                   └─────────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. 存储引擎接口

### 1.1 KVDatabase（KV 数据库）

基于 LMDB 的键值存储，支持任意 JSON 可序列化对象的存取。

**LMDB 特点**：
- 嵌入式 B+树，内存映射文件
- 读性能极快，支持多线程并发读
- 单线程写（通过线程锁控制）
- 默认 100MB 映射文件，可配置

```python
class KVDatabase:
    """
    KV 数据库接口

    基于 LMDB 实现，提供高性能键值存储能力。

    目录结构：
        persist_directory/
        └── kv_{db_name}.lmdb/

    TTL 实现：
        - 值中存储 expire_at 时间戳
        - 读取时检查是否过期
    """

    def __init__(
        self,
        persist_directory: str,
        db_name: str = "default",
        map_size: int = 100 * 1024 * 1024,
        writemap: bool = False,
    ):
        """
        初始化 KV 数据库

        Args:
            persist_directory: 持久化根目录
            db_name: 数据库名称
            map_size: LMDB 映射文件大小（字节），默认 100MB
            writemap: 是否使用写入映射（更快但兼容性差）
        """

    # ---- 基础操作 ----

    def set(self, key: str, value: Any) -> bool:
        """
        设置键值对

        Args:
            key: 键名（字符串）
            value: 值（必须是 JSON 可序列化对象）

        Returns:
            是否成功
        """

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取值

        Args:
            key: 键名
            default: 默认值（key 不存在时返回）

        Returns:
            存储的值或默认值
        """

    def delete(self, key: str) -> bool:
        """
        删除键值对

        Args:
            key: 键名

        Returns:
            是否成功删除
        """

    def exists(self, key: str) -> bool:
        """检查 key 是否存在"""

    def keys(self, pattern: str = "*") -> List[str]:
        """
        返回所有匹配的键

        Args:
            pattern: Glob 模式，如 "mem_*", "user:*"
        """

    def clear(self) -> int:
        """清空所有数据，返回删除的键数量"""

    # ---- 批量操作 ----

    def mset(self, items: Dict[str, Any]) -> bool:
        """批量设置"""

    def mget(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取，返回存在的键值对"""

    def mdelete(self, keys: List[str]) -> int:
        """批量删除，返回删除数量"""

    # ---- 特殊操作 ----

    def expire(self, key: str, ttl_seconds: int) -> bool:
        """
        设置过期时间（TTL）

        Args:
            key: 键名
            ttl_seconds: 过期秒数

        Returns:
            是否成功
        """

    def ttl(self, key: str) -> int:
        """
        获取剩余生存时间

        Returns:
            剩余秒数，-1 表示永不过期，-2 表示不存在
        """

    # ---- 迭代器 ----

    def scan(self, pattern: str = "*", batch_size: int = 100):
        """
        游标迭代遍历所有键值对

        Args:
            pattern: Glob 模式
            batch_size: 每批返回数量

        Yields:
            (key, value) 元组
        """
```

### 1.2 SQLDatabase（SQL 数据库）

基于 SQLite 的通用 SQL 接口，支持原生 SQL 执行和事务。

```python
class SQLDatabase:
    """
    SQL 数据库接口

    基于 SQLite 实现，提供完整的 SQL 执行能力。

    目录结构：
        persist_directory/
        └── sql_{db_name}.sqlite3
    """

    def __init__(self, persist_directory: str, db_name: str = "default"):
        """
        初始化 SQL 数据库

        Args:
            persist_directory: 持久化根目录
            db_name: 数据库名称
        """

    # ---- DDL 操作 ----

    def create_table(
        self,
        table_name: str,
        columns: Dict[str, str],
        if_not_exists: bool = True,
    ) -> bool:
        """
        创建表

        Args:
            table_name: 表名
            columns: 列定义，如 {"id": "TEXT PRIMARY KEY", "name": "TEXT NOT NULL"}
            if_not_exists: 是否添加 IF NOT EXISTS

        Returns:
            是否成功
        """

    def drop_table(self, table_name: str, if_exists: bool = True) -> bool:
        """删除表"""

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""

    # ---- DML 操作 ----

    def insert(
        self,
        table_name: str,
        data: Dict[str, Any],
        or_replace: bool = False,
    ) -> bool:
        """
        插入数据

        Args:
            table_name: 表名
            data: 列名到值的映射
            or_replace: 是否使用 OR REPLACE

        Returns:
            是否成功
        """

    def insert_many(
        self,
        table_name: str,
        data_list: List[Dict[str, Any]],
    ) -> int:
        """
        批量插入

        Args:
            table_name: 表名
            data_list: 数据列表

        Returns:
            插入的行数
        """

    def update(
        self,
        table_name: str,
        data: Dict[str, Any],
        where: Dict[str, Any],
    ) -> int:
        """
        更新数据

        Args:
            table_name: 表名
            data: 要更新的列和值
            where: WHERE 条件（AND 连接）

        Returns:
            影响的行数
        """

    def delete(
        self,
        table_name: str,
        where: Dict[str, Any],
    ) -> int:
        """
        删除数据

        Args:
            table_name: 表名
            where: WHERE 条件

        Returns:
            影响的行数
        """

    # ---- 查询操作 ----

    def select(
        self,
        table_name: str,
        columns: List[str] = None,
        where: Dict[str, Any] = None,
        order_by: str = None,
        order: str = "ASC",
        limit: int = None,
        offset: int = None,
    ) -> List[Dict[str, Any]]:
        """
        查询数据

        Args:
            table_name: 表名
            columns: 要查询的列（None 表示所有）
            where: WHERE 条件
            order_by: 排序列名
            order: ASC 或 DESC
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            查询结果列表
        """

    def query(self, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """
        执行原生 SQL 查询

        Args:
            sql: SQL 语句（应使用 ? 占位符）
            params: 查询参数

        Returns:
            查询结果列表
        """

    def execute(self, sql: str, params: Tuple = ()) -> bool:
        """
        执行原生 SQL（用于 INSERT/UPDATE/DELETE）

        Args:
            sql: SQL 语句
            params: 参数

        Returns:
            是否成功
        """

    # ---- 事务支持 ----

    def begin(self):
        """开启事务"""

    def commit(self):
        """提交事务"""

    def rollback(self):
        """回滚事务"""

    def transaction(self):
        """
        上下文管理器，自动提交/回滚

        Usage:
            with db.transaction():
                db.insert(...)
                db.update(...)
        """

    # ---- 工具方法 ----

    def count(self, table_name: str, where: Dict[str, Any] = None) -> int:
        """返回行数"""

    def clear(self, table_name: str = None):
        """
        清空数据

        Args:
            table_name: 表名（None 表示清空所有表）
        """
```

---

## 2. 解释器（Interpreter）

### 2.1 设计目标

解释器提供一种 DSL 语法，可以在运行时绑定对象，并通过表达式调用其方法。

### 2.2 语法规范

```
表达式格式：
    <对象名>.<方法名>(<参数名>=<值>, ...)

示例：
    crud.read(memory_id="mem_abc123")
    kvdb.get(key="user:1001")
    sql.select(table_name="memories", limit=10)
```

### 2.3 API 设计

```python
class Interpreter:
    """
    解释器

    绑定运行时对象，通过 DSL 表达式调用方法。

    语法：
        <对象名>.<方法名>(<参数名>=<值>, ...)

    示例：
        inter = Interpreter()
        inter.bind("crud", crud_instance)
        result = inter.eval("crud.read(memory_id='mem_123')")
    """

    def __init__(self):
        """初始化解释器"""
        self._context: Dict[str, Any] = {}

    # ---- 绑定管理 ----

    def bind(self, name: str, obj: Any) -> "Interpreter":
        """
        绑定对象到命名空间

        Args:
            name: 对象名（用于表达式中引用）
            obj: 对象实例

        Returns:
            self（支持链式调用）
        """

    def unbind(self, name: str) -> bool:
        """解除绑定"""

    def bound_names(self) -> List[str]:
        """返回所有已绑定的名称"""

    def get_bound(self, name: str) -> Any:
        """获取绑定的对象"""

    # ---- 表达式执行 ----

    def eval(self, expression: str) -> Any:
        """
        执行表达式

        Args:
            expression: DSL 表达式，如 "obj.method(arg=1)"

        Returns:
            方法调用的返回值

        Raises:
            InterpreterError: 表达式解析或执行错误
        """

    def execute(self, statements: str) -> List[Any]:
        """
        执行多条语句（换行分隔）

        Args:
            statements: 多行语句

        Returns:
            各语句的返回值列表
        """

    # ---- 变量访问 ----

    def set_var(self, name: str, value: Any):
        """设置临时变量（仅在当前解释器会话有效）"""

    def get_var(self, name: str) -> Any:
        """获取变量值"""

    def clear_vars(self):
        """清空所有变量"""
```

### 2.4 解析规则

| 语法 | 示例 | 解析结果 |
|------|------|----------|
| `obj.method()` | `crud.read()` | 调用 `crud.read()` |
| `obj.method(arg=1)` | `kv.set(key="a", value=1)` | 调用 `kv.set(key="a", value=1)` |
| `obj.method(arg="str")` | `sql.select(table="a", limit=10)` | 调用 `sql.select(table="a", limit=10)` |
| 字符串值 | `arg="hello"` 或 `arg='hello'` | 解析为 Python 字符串 |
| 数字值 | `arg=123` 或 `arg=3.14` | 解析为 int 或 float |
| 布尔值 | `arg=true` / `arg=false` | 解析为 True / False |
| None 值 | `arg=null` | 解析为 None |

### 2.5 错误处理

```python
class InterpreterError(Exception):
    """解释器异常"""
    pass

class BindingNotFoundError(InterpreterError):
    """绑定对象未找到"""
    pass

class MethodNotFoundError(InterpreterError):
    """方法不存在"""
    pass

class ParseError(InterpreterError):
    """语法解析错误"""
    pass
```

---

## 3. 调度器（Scheduler）

### 3.1 设计目标

调度器统一管理多个 KV 库和 SQL 库实例，提供：
- 按路径创建/获取/删除数据库
- 实例缓存，避免重复创建
- 生命周期管理

### 3.2 API 设计

```python
class Scheduler:
    """
    数据库调度器

    管理 KV 数据库和 SQL 数据库的生命周期。

    目录结构：
        base_directory/
        ├── kv/              # KV 数据库
        │   └── kv_{db_name}.lmdb/  (LMDB 目录)
        └── sql/             # SQL 数据库
            └── sql_{db_name}.sqlite3
    """

    def __init__(self, base_directory: str = "./data/aux_db"):
        """
        初始化调度器

        Args:
            base_directory: 持久化根目录
        """

    # ---- KV 数据库管理 ----

    def create_kv(self, db_name: str) -> KVDatabase:
        """
        创建或获取 KV 数据库

        Args:
            db_name: 数据库名称

        Returns:
            KVDatabase 实例
        """

    def get_kv(self, db_name: str) -> Optional[KVDatabase]:
        """
        获取已创建的 KV 数据库

        Args:
            db_name: 数据库名称

        Returns:
            KVDatabase 实例，不存在返回 None
        """

    def delete_kv(self, db_name: str) -> bool:
        """
        删除 KV 数据库

        Args:
            db_name: 数据库名称

        Returns:
            是否成功
        """

    def kv_exists(self, db_name: str) -> bool:
        """检查 KV 数据库是否存在"""

    def list_kv(self) -> List[str]:
        """列出所有 KV 数据库"""

    # ---- SQL 数据库管理 ----

    def create_sql(self, db_name: str) -> SQLDatabase:
        """
        创建或获取 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            SQLDatabase 实例
        """

    def get_sql(self, db_name: str) -> Optional[SQLDatabase]:
        """
        获取已创建的 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            SQLDatabase 实例，不存在返回 None
        """

    def delete_sql(self, db_name: str) -> bool:
        """
        删除 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            是否成功
        """

    def sql_exists(self, db_name: str) -> bool:
        """检查 SQL 数据库是否存在"""

    def list_sql(self) -> List[str]:
        """列出所有 SQL 数据库"""

    # ---- 批量操作 ----

    def create_all(self, kv_names: List[str] = None, sql_names: List[str] = None):
        """
        批量创建数据库

        Args:
            kv_names: KV 数据库名称列表
            sql_names: SQL 数据库名称列表
        """

    def delete_all(self):
        """删除所有数据库"""

    def list_all(self) -> Dict[str, List[str]]:
        """
        列出所有数据库

        Returns:
            {"kv": [...], "sql": [...]}
        """

    # ---- 生命周期 ----

    def close(self):
        """关闭所有数据库连接"""

    def close_kv(self, db_name: str):
        """关闭指定 KV 数据库"""

    def close_sql(self, db_name: str):
        """关闭指定 SQL 数据库"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
```

### 3.3 使用示例

```python
# 创建调度器
scheduler = Scheduler("./data/aux_db")

# 创建 KV 数据库
kv = scheduler.create_kv("memory_cache")
kv.set("user:1001", {"name": "张三", "age": 30})

# 创建 SQL 数据库
sql = scheduler.create_sql("analytics")
sql.create_table("events", {
    "id": "INTEGER PRIMARY KEY",
    "type": "TEXT NOT NULL",
    "timestamp": "INTEGER NOT NULL",
})

# 通过解释器使用
inter = Interpreter()
inter.bind("kv", scheduler.create_kv("cache"))
inter.bind("sql", scheduler.create_sql("logs"))

# 使用 DSL 调用
result = inter.eval("kv.get(key='user:1001')")
events = inter.eval("sql.select(table_name='events', limit=10)")

# 关闭
scheduler.close()
```

---

## 4. 应用索引

### 4.1 TimeIndex（时间索引）

#### 设计背景

记忆的时间词可能是"平时"、"经常"、"有时候"等模糊表达，而查询时想知道"最近一周"或"今天"的相关记忆。时间索引需要：
- 支持按时间词模糊匹配
- 支持按 datetime 范围查询
- 与记忆的生命周期管理联动

#### API 设计

```python
class TimeIndex:
    """
    时间索引表

    索引结构：
        1. time_word → set[memory_id]（时间词倒排索引）
        2. created_at → set[memory_id]（时间戳索引，用于范围查询）

    语义映射：
        平时 → 通常时期
        经常 → 高频
        偶尔 → 低频
        最近 → 30天内
        今天 → 当天
    """

    def __init__(self, sql_db: SQLDatabase):
        """
        初始化时间索引

        Args:
            sql_db: SQL 数据库实例（由 Scheduler 管理）
        """

    def add(self, memory_id: str, time_word: str, created_at: datetime):
        """
        写入时注册时间索引

        Args:
            memory_id: 记忆 ID
            time_word: 时间词，如"平时"、"经常"
            created_at: 创建时间
        """

    def remove(self, memory_id: str):
        """删除记忆的时间索引"""

    def query_by_range(
        self,
        start: datetime,
        end: datetime,
    ) -> List[str]:
        """
        按时间范围查询

        Args:
            start: 开始时间
            end: 结束时间

        Returns:
            匹配的 memory_id 列表
        """

    def query_by_words(
        self,
        time_words: List[str],
        fuzzy: bool = True,
    ) -> List[str]:
        """
        按时间词查询

        Args:
            time_words: 时间词列表，如["平时", "经常"]
            fuzzy: 是否启用模糊匹配

        Returns:
            匹配的 memory_id 列表
        """

    def query_recent(self, days: int = 7) -> List[str]:
        """
        查询最近 N 天的记忆

        Args:
            days: 天数

        Returns:
            匹配的 memory_id 列表
        """

    def get_time_words(self, memory_id: str) -> List[str]:
        """获取记忆对应的时间词列表"""
```

#### 语义映射表

| 查询词 | 语义 | 时间范围 |
|--------|------|----------|
| 今天 | 当天 | 00:00:00 ~ 23:59:59 |
| 昨天 | 前一天 | 同上 |
| 最近 | 最近 N 天 | configurable |
| 经常 | 高频动作 | time_word = "经常" |
| 平时 | 一般时期 | time_word = "平时" |
| 有时候 | 低频动作 | time_word = "有时候" |

### 4.2 SlotIndex（槽位索引）

#### 设计背景

用户可能想知道某个词在哪些记忆的哪个槽位出现过。例如：
- "编程" 在哪些记忆的 object 槽位出现过？
- "学习" 在哪些记忆的 purpose 槽位出现过？

#### API 设计

```python
class SlotIndex:
    """
    查询句槽位索引

    使用原始嵌入（RawEmbedding）构建按槽位分区的 ANN 索引。

    索引结构：
        6 个 Chroma Collection（各 64 维）：
            slot_time, slot_subject, slot_action,
            slot_object, slot_purpose, slot_result

        每条记录包含：
            - memory_id
            - slot_vector (64维)
            - slot_value (字符串)
    """

    def __init__(
        self,
        chroma_path: str,
        sql_db: SQLDatabase,
        slot_dim: int = 64,
    ):
        """
        初始化槽位索引

        Args:
            chroma_path: Chroma 持久化路径
            sql_db: SQL 数据库（用于存储 metadata）
            slot_dim: 槽位向量维度
        """

    def add(
        self,
        memory_id: str,
        slot_vectors: Dict[str, np.ndarray],
        slot_values: Dict[str, str],
    ):
        """
        写入时注册槽位索引

        Args:
            memory_id: 记忆 ID
            slot_vectors: 各槽位向量，如 {"scene": [64维], "subject": [64维], ...}
            slot_values: 各槽位字符串值，如 {"scene": "平时", "subject": "我", ...}
        """

    def remove(self, memory_id: str):
        """删除记忆的槽位索引"""

    def find_by_word(
        self,
        word: str,
        slot: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        按词查找（在指定槽位中搜索）

        Args:
            word: 要查找的词
            slot: 槽位名（scene/subject/action/object/purpose/result）
            top_k: 返回数量

        Returns:
            [(memory_id, distance), ...]
        """

    def find_by_vector(
        self,
        vector: np.ndarray,
        slot: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        按向量查找（在指定槽位中搜索）

        Args:
            vector: 查询向量 [64维]
            slot: 槽位名
            top_k: 返回数量

        Returns:
            [(memory_id, distance), ...]
        """

    def find_in_all_slots(
        self,
        word: str,
        top_k: int = 5,
    ) -> Dict[str, List[Tuple[str, float]]]:
        """
        在所有槽位中查找词

        Args:
            word: 要查找的词
            top_k: 每槽位返回数量

        Returns:
            {slot_name: [(memory_id, distance), ...], ...}
        """

    def get_slot_value(self, memory_id: str, slot: str) -> Optional[str]:
        """获取记忆指定槽位的字符串值"""
```

---

## 5. 模块文件结构

```
auxiliary_query/
├── __init__.py          # 模块入口
├── DESIGN.md            # 本文档
├── storage/
│   ├── __init__.py
│   ├── base.py          # 存储引擎基类
│   ├── kv_db.py         # KVDatabase 实现
│   └── sql_db.py        # SQLDatabase 实现
├── interpreter/
│   ├── __init__.py
│   ├── core.py          # Interpreter 核心
│   ├── parser.py        # DSL 语法解析器
│   └── errors.py        # 异常定义
├── scheduler/
│   ├── __init__.py
│   └── core.py          # Scheduler 实现
└── indexes/
    ├── __init__.py
    ├── time_index.py    # TimeIndex 实现
    └── slot_index.py    # SlotIndex 实现
```

---

## 6. 使用示例

### 6.1 完整工作流

```python
from xcmemory_interest.auxiliary_query import (
    Scheduler,
    Interpreter,
    TimeIndex,
    SlotIndex,
)
from xcmemory_interest.basic_crud import VecDBCRUD

# 1. 创建调度器
scheduler = Scheduler("./data/aux_db")

# 2. 初始化主存储
crud = VecDBCRUD(persist_directory="./data/xcmemory_db")

# 3. 初始化应用索引
time_index = TimeIndex(sql_db=scheduler.create_sql("time_index"))
slot_index = SlotIndex(
    chroma_path="./data/aux_db/slot_index",
    sql_db=scheduler.create_sql("slot_index_meta"),
)

# 4. 创建解释器
inter = Interpreter()
inter.bind("crud", crud)
inter.bind("kv", scheduler.create_kv("cache"))
inter.bind("sql", scheduler.create_sql("logs"))
inter.bind("ti", time_index)
inter.bind("si", slot_index)

# 5. 写入记忆
memory_id = crud.write(
    query_sentence="<平时><我><学><编程><为了><进步>",
    content="我学习编程是为了进步",
    lifecycle=100,
)

# 6. 注册索引
slots = crud._slots_from_sentence("...")
slot_vecs = crud.pipeline.get_slot_vectors(slots)
parts = crud._parse_query_sentence("...")
slot_values = {name: parts[i] for i, name in enumerate(crud.SLOT_NAMES)}

time_index.add(memory_id, parts[0], datetime.now())
slot_index.add(memory_id, slot_vecs, slot_values)

# 7. 使用解释器查询
# 查询最近一周的记忆
recent = inter.eval("ti.query_recent(days=7)")

# 查找"编程"出现在哪些记忆的对象槽位
programming_memories = inter.eval(
    "si.find_by_word(word='编程', slot='object', top_k=10)"
)

# 直接用 DSL 操作
result = inter.eval("crud.read(memory_id='{}')".format(memory_id))

# 8. 关闭
scheduler.close()
```

### 6.2 DSL 表达式示例

```python
# 读取记忆
inter.eval("crud.read(memory_id='mem_abc123')")

# 搜索记忆
inter.eval("crud.search_subspace(query_slots={'subject': '我'}, top_k=5)")

# KV 数据库操作
inter.eval("kv.set(key='cache:1', value={'data': 'test'})")
inter.eval("kv.get(key='cache:1')")
inter.eval("kv.mset(items={'a': 1, 'b': 2})")

# SQL 数据库操作
inter.eval("sql.select(table_name='memories', limit=10)")
inter.eval("sql.query(sql='SELECT * FROM memories WHERE lifecycle > ?', params=(50,))")

# 时间索引操作
inter.eval("ti.query_recent(days=7)")
inter.eval("ti.query_by_words(time_words=['平时', '经常'])")

# 槽位索引操作
inter.eval("si.find_by_word(word='编程', slot='object')")
```

---

## 7. 待讨论

- [ ] 时间词到 datetime 的映射规则（见 TimeIndex.semantic_map）
- [ ] 槽位索引和主向量索引的同步策略
- [ ] 原始嵌入索引的更新机制
- [ ] 解释器安全性（是否允许任意代码执行）
