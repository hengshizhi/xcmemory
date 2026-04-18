# 星尘记忆系统 - API 参考

> 星尘记忆系统 (xcmemory_interest) v0.4.0

## 目录

1. [模块导入](#1-模块导入)
2. [PyAPI](#2-pyapi)
3. [MemorySystem](#3-memorysystem)
4. [VecDBCRUD](#4-vecdbcrud)
5. [MQL](#5-mql)
6. [UserManager](#6-usermanager)
7. [VersionManager](#7-versionmanager)
8. [数据类型](#8-数据类型)
9. [HTTP API](#9-http-api)
10. [WebSocket API](#10-websocket-api)

---

## 1. 模块导入

```python
# 主要接口
from xcmemory_interest import (
    PyAPI,
    MemorySystem,
    VecDBCRUD,
    Interpreter,
    parse,
    MQLResult,
    MQLError,
    ParseError,
)

# 向量数据库
from xcmemory_interest import (
    ChromaVectorDB,
    SubspaceSearcher,
    HybridSearcher,
    SubspaceReranker,
    DynamicReranker,
)

# 嵌入编码
from xcmemory_interest import (
    InterestEncoder,
    QueryEncoder,
)

# 生命周期
from xcmemory_interest import (
    LifecycleManager,
    ProbabilitySampler,
    LIFECYCLE_INFINITY,
)

# 版本控制
from xcmemory_interest import (
    VersionManager,
    MemoryVersion,
    VersionDiff,
    ChangeType,
)

# 用户管理
from xcmemory_interest.user_manager import (
    UserManager,
    PermissionType,
    AuthContext,
)

# 网络 API
from xcmemory_interest.netapi import APIServer
```

---

## 2. PyAPI

多数据库管理入口。

### 2.1 初始化

```python
api = PyAPI(database_root: str)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `database_root` | `str` | 根目录，管理多个记忆系统 |

### 2.2 方法

#### `create_system()`

```python
system = api.create_system(
    name: str,
    enable_interest_mode: bool = False,
    similarity_threshold: float = 0.5,
) -> MemorySystem
```

创建新的记忆系统。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | - | 系统名称 |
| `enable_interest_mode` | `bool` | `False` | 启用兴趣模型（当前不支持） |
| `similarity_threshold` | `float` | `0.5` | 相似度阈值 |

**抛出**：`NotImplementedError` - 当 `enable_interest_mode=True`

#### `get_system()`

```python
system = api.get_system(name: str) -> MemorySystem
```

获取记忆系统实例。

#### `list_systems()`

```python
systems = api.list_systems() -> List[str]
```

列出所有系统名称。

#### `set_active_system()`

```python
api.set_active_system(name: str)
```

切换活跃系统，后续 `execute()` 在此系统上执行。

#### `delete_system()`

```python
api.delete_system(name: str)
```

删除记忆系统（同时删除所有数据）。

#### `execute()`

```python
result = api.execute(mql: str) -> MQLResult
```

在活跃系统上执行 MQL 语句。

---

## 3. MemorySystem

单个记忆系统。

### 3.1 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 系统名称 |
| `crud` | `VecDBCRUD` | CRUD 操作器 |
| `version_manager` | `VersionManager` | 版本管理器 |
| `lifecycle_manager` | `LifecycleManager` | 生命周期管理器 |
| `scheduler` | `Scheduler` | 查询调度器 |

### 3.2 方法

#### `execute()`

```python
result = system.execute(mql: str) -> MQLResult
```

执行 MQL 语句。

#### `insert()`

```python
memory_id = system.insert(
    query_sentence: str,
    content: str,
    lifecycle: int = None,
) -> str
```

插入记忆。`lifecycle` 为可选参数，传入 `None` 或省略时由 LifecycleManager 根据查询句自动决定。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query_sentence` | `str` | 6 槽位查询句 |
| `content` | `str` | 记忆内容 |
| `lifecycle` | `int` | 可选。生命周期秒数，`None` 时由 LifecycleManager 决定 |

#### `search()`

```python
memories = system.search(
    query_embedding: np.ndarray,
    top_k: int = 5,
    filter_conditions: dict = None,
) -> List[Memory]
```

向量搜索。

#### `get()`

```python
memory = system.get(memory_id: str) -> Optional[Memory]
```

获取单条记忆。

#### `update()`

```python
success = system.update(memory_id: str, **kwargs) -> bool
```

更新记忆字段。

#### `delete()`

```python
success = system.delete(memory_id: str) -> bool
```

删除记忆。

---

## 4. VecDBCRUD

向量数据库 CRUD 操作器。

### 4.1 初始化

```python
crud = VecDBCRUD(
    persist_directory: str,
    vocab_size: int = 32000,
    slot_dim: int = 64,
    enable_interest_mode: bool = False,
    similarity_threshold: float = 0.5,
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `persist_directory` | `str` | - | 持久化目录 |
| `vocab_size` | `int` | `32000` | 词表大小 |
| `slot_dim` | `int` | `64` | 槽位向量维度 |
| `enable_interest_mode` | `bool` | `False` | 兴趣模式（不支持） |
| `similarity_threshold` | `float` | `0.5` | 相似度阈值 |

### 4.2 方法

#### `create_collections()`

```python
crud.create_collections()
```

创建所有 Chroma collection（首次初始化时调用）。

#### `insert()`

```python
memory_id = crud.insert(
    query_sentence: str,
    content: str,
    lifecycle: int,
    query_embedding: np.ndarray,
    raw_embedding: np.ndarray,
) -> str
```

#### `search()`

```python
results = crud.search(
    query_embedding: np.ndarray,
    top_k: int = 5,
    filter_conditions: dict = None,
) -> List[SearchResult]
```

#### `get()`

```python
memory = crud.get(memory_id: str) -> Optional[Memory]
```

#### `update()`

```python
success = crud.update(memory_id: str, **kwargs) -> bool
```

#### `delete()`

```python
success = crud.delete(memory_id: str) -> bool
```

#### `list_by_filter()`

```python
memories = crud.list_by_filter(
    conditions: List[Condition],
    limit: int = 100,
) -> List[Memory]
```

---

## 5. MQL

### 5.1 `parse()`

```python
ast = parse(mql: str) -> Statement
```

解析 MQL 字符串为 AST。

### 5.2 `Interpreter`

```python
interpreter = Interpreter()
interpreter.bind("mem", memory_system)
interpreter.bind("api", pyapi)
interpreter.bind("um", user_manager)
interpreter.set_auth_context(auth_context)

result = interpreter.execute(mql: str) -> MQLResult
results = interpreter.execute_script(script: str) -> List[MQLResult]
```

### 5.3 MQLResult

```python
@dataclass
class MQLResult:
    type: str           # "select", "insert", "update", "delete", ...
    data: List[dict]    # 查询结果数据
    affected_rows: int  # 影响行数
    memory_ids: List[str]  # 涉及的 memory_id
    message: str        # 状态消息
```

### 5.4 错误类型

```python
from xcmemory_interest.mql import MQLError, ParseError, ExecutionError

try:
    result = interpreter.execute("INVALID MQL")
except ParseError as e:
    print(f"语法错误: {e}")
except ExecutionError as e:
    print(f"执行错误: {e}")
except MQLError as e:
    print(f"MQL 错误: {e}")
```

---

## 6. UserManager

用户认证和权限管理。

### 6.1 初始化

```python
um = UserManager(persist_directory: str)
```

### 6.2 方法

#### `generate_api_key()`

```python
api_key = um.generate_api_key(username: str) -> str
```

为用户生成/重置 APIKey，返回完整的 `xi-<username>-<key>` 格式。

#### `authenticate()`

```python
auth = um.authenticate(api_key: str) -> AuthContext
```

验证 APIKey，返回认证上下文。

#### `create_user()`

```python
um.create_user(username: str)
```

创建用户。

#### `delete_user()`

```python
um.delete_user(username: str)
```

删除用户（不能删除 admin）。

#### `list_users()`

```python
users = um.list_users() -> List[str]
```

列出所有用户名。

#### `grant_permission()`

```python
um.grant_permission(
    username: str,
    system: str,
    permission: PermissionType,
)
```

授予权限。

#### `revoke_permission()`

```python
um.revoke_permission(
    username: str,
    system: str,
    permission: PermissionType,
)
```

撤销权限。

#### `list_permissions()`

```python
perms = um.list_permissions(username: str) -> List[Permission]
```

列出用户的所有权限。

#### `has_permission()`

```python
has = um.has_permission(
    username: str,
    system: str,
    permission: PermissionType,
) -> bool
```

检查权限。

### 6.3 PermissionType

```python
from xcmemory_interest.user_manager import PermissionType

PermissionType.READ        # 只读
PermissionType.WRITE       # 只写
PermissionType.READ_WRITE  # 读写
PermissionType.VERSION_COMMIT  # 版本提交
PermissionType.VERSION_DELETE  # 版本删除
PermissionType.ADMIN       # 用户管理
```

### 6.4 AuthContext

```python
@dataclass
class AuthContext:
    username: str
    system: str
    permission: PermissionType
    is_admin: bool
```

---

## 7. VersionManager

记忆版本管理。

### 7.1 初始化

```python
vm = VersionManager(persist_directory: str)
```

### 7.2 方法

#### `commit()`

```python
version = vm.commit(memory_id: str, memory_data: dict) -> MemoryVersion
```

提交新版本。

#### `get_version()`

```python
version = vm.get_version(memory_id: str, version: int) -> MemoryVersion
```

获取历史版本。

#### `list_versions()`

```python
versions = vm.list_versions(memory_id: str) -> List[MemoryVersion]
```

列出所有版本。

#### `diff()`

```python
diff = vm.diff(memory_id: str, v1: int, v2: int) -> VersionDiff
```

比较两个版本差异。

#### `rollback()`

```python
success = vm.rollback(memory_id: str, version: int) -> bool
```

回滚到指定版本。

---

## 8. 数据类型

### 8.1 Memory

```python
@dataclass
class Memory:
    id: str
    query_sentence: str           # "<时间><主体><动作><宾语><目的><结果>"
    query_embedding: np.ndarray    # 兴趣嵌入 [384]
    raw_embedding: np.ndarray      # 原始嵌入 [384]
    content: str                   # 记忆内容
    lifecycle: int                 # 生命周期（秒）
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict
    @classmethod
    def from_dict(cls, d: dict) -> Memory
```

### 8.2 SearchResult

```python
@dataclass
class SearchResult:
    memory_id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)
    sort_by: Optional[str] = None
    match_count: int = 0
    avg_distance: float = 0.0
```

### 8.3 Condition

```python
@dataclass
class Condition:
    field: str      # 字段名
    op: str         # 操作符: "=", "!=", "<", ">", "<=", ">=", "LIKE", "IN"
    value: Any      # 值
    combinator: str = "AND"  # "AND", "OR"
```

### 8.4 LifecycleQueryResult

```python
@dataclass
class LifecycleQueryResult:
    memory_id: str
    lifecycle: int
    is_expired: bool
    expires_at: Optional[datetime] = None
```

---

## 9. HTTP API

### 9.1 APIServer

```python
server = APIServer(
    database_root: str = "./data",
    host: str = "0.0.0.0",
    port: int = 8080,
    ws_port: int = None,
    max_request_size: int = 10 * 1024 * 1024,
)

server.start(blocking: bool = True)
server.stop()
```

### 9.2 端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/health` | 健康检查 | 否 |
| POST | `/api/v1/query` | 执行 MQL | 是 |
| GET | `/api/v1/systems` | 列出系统 | 是 |
| POST | `/api/v1/systems` | 创建系统 | 是 |
| GET | `/api/v1/systems/<name>` | 获取系统信息 | 是 |
| DELETE | `/api/v1/systems/<name>` | 删除系统 | 管理员 |
| POST | `/api/v1/systems/<name>/use` | 切换系统 | 是 |
| GET | `/api/v1/users` | 列出用户 | 管理员 |
| POST | `/api/v1/users` | 创建用户 | 管理员 |
| GET | `/api/v1/users/<username>` | 获取用户信息 | 管理员 |
| DELETE | `/api/v1/users/<username>` | 删除用户 | 管理员 |
| POST | `/api/v1/users/<username>/generate_key` | 生成 APIKey | 管理员 |
| POST | `/api/v1/permissions` | 授予权限 | 管理员 |
| DELETE | `/api/v1/permissions` | 撤销权限 | 管理员 |

### 9.3 请求格式

#### POST `/api/v1/query`

```json
// 单条 MQL
{
    "mql": "SELECT * FROM memories LIMIT 5"
}

// 多行 MQL
{
    "script": "CREATE DATABASE test; USE test; SELECT * FROM memories;"
}
```

#### POST `/api/v1/systems`

```json
{
    "name": "my_system",
    "enable_interest_mode": false,
    "similarity_threshold": 0.5
}
```

#### POST `/api/v1/users`

```json
{
    "username": "alice"
}
```

#### POST `/api/v1/permissions`

```json
{
    "username": "alice",
    "system": "my_system",
    "permission": "read_write"
}
```

### 9.4 响应格式

```json
{
    "type": "result",
    "success": true,
    "data": [...],
    "affected_rows": 1,
    "memory_ids": ["mem_xxx"],
    "message": "OK"
}
```

---

## 10. WebSocket API

### 10.1 连接

```
ws://localhost:8081/ws
```

### 10.2 消息类型

#### 认证

```json
{
    "type": "auth",
    "api_key": "xi-admin-xxx"
}
```

#### 查询

```json
{
    "type": "mql",
    "mql": "SELECT * FROM memories LIMIT 5"
}
```

#### 多行脚本

```json
{
    "type": "mql",
    "script": "CREATE DATABASE x; USE x; SELECT * FROM memories;"
}
```

#### 切换系统

```json
{
    "type": "use",
    "system": "my_system"
}
```

#### 心跳

```json
{
    "type": "ping"
}
```

### 10.3 响应类型

#### 认证成功

```json
{
    "type": "auth",
    "success": true,
    "username": "admin"
}
```

#### 查询结果

```json
{
    "type": "result",
    "success": true,
    "data": [...],
    "affected_rows": 1,
    "memory_ids": ["mem_xxx"],
    "message": "OK"
}
```

#### 多行结果

```json
{
    "type": "result",
    "success": true,
    "data": [...],
    "script_results": [
        {"type": "result", "data": [], "affected": 0, "message": "Created"},
        {"type": "result", "data": [...], "affected": 5, "message": "OK"}
    ]
}
```

#### 错误

```json
{
    "type": "error",
    "error": "Not authenticated"
}
```

---

## 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `LIFECYCLE_INFINITY` | `-1` | 永久记忆 |
| `DEFAULT_SIMILARITY_THRESHOLD` | `0.5` | 默认相似度阈值 |
| `SLOT_DIM` | `64` | 槽位向量维度 |
| `FULL_DIM` | `384` | 全量向量维度 |
| `NUM_SLOTS` | `6` | 槽位数量 |
