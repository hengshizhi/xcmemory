# 星尘记忆系统 - 使用文档

> 星尘记忆系统 (xcmemory_interest) v0.4.0

## 目录

1. [系统简介](#1-系统简介)
2. [快速开始](#2-快速开始)
3. [核心概念](#3-核心概念)
4. [MQL 查询语言](#4-mql-查询语言)
5. [Python API](#5-python-api)
6. [HTTP API](#6-http-api)
7. [WebSocket API](#7-websocket-api)
8. [常见问题](#8-常见问题)

---

## 1. 系统简介

星尘记忆系统是一个基于向量检索的结构化记忆管理系统，支持：

- **6 槽位记忆结构**：时间、主体、动作、宾语、目的、结果
- **向量搜索**：基于语义相似度的记忆检索
- **生命周期管理**：自动过期和衰减
- **多数据库支持**：一个根目录管理多个独立记忆系统
- **MQL 查询语言**：类 SQL 的字符串查询语法
- **API 鉴权**：用户级别权限控制

---

## 2. 快速开始

### 2.1 安装依赖

```bash
cd o:/project/starlate
o:/project/starlate/venv/Scripts/pip.exe install -r requirements.txt
```

### 2.2 最简使用

```python
from xcmemory_interest import PyAPI

# 初始化（根目录管理多个记忆系统）
api = PyAPI("./data/xcmemory")

# 创建记忆系统（默认不启用兴趣模型）
system = api.create_system("my_memory")

# 插入记忆（MQL 语句）
system.execute("""
    INSERT INTO memories VALUES (
        '<平时><我><学><编程><喜欢><有收获>',
        '我喜欢学编程，感觉很有成就感',
        86400
    )
""")

# 查询记忆
result = system.execute("SELECT * FROM memories LIMIT 5")
for row in result.data:
    print(row)
```

### 2.3 运行 HTTP API 服务

```python
from xcmemory_interest.netapi import APIServer

server = APIServer(
    database_root="./data/xcmemory",
    host="0.0.0.0",
    port=8080,
    ws_port=8081,  # WebSocket 端口
)
server.start()
```

---

## 3. 核心概念

### 3.1 记忆结构（6 槽位）

每条记忆由 6 个槽位组成：

| 槽位 | 名称 | 说明 | 示例 |
|------|------|------|------|
| time | 时间词 | 动作发生的时间背景 | 平时、经常、偶尔 |
| subject | 主体 | 执行动作的实体 | 我、他、我们 |
| action | 动作 | 主要行为 | 学、看、玩 |
| object | 宾语 | 动作的客体 | 书、游戏、实验 |
| purpose | 目的 | 动作的目的/原因 | 学习进步、喜欢 |
| result | 结果 | 动作产生的结果 | 成功了、有收获 |

**格式**：`"<时间><主体><动作><宾语><目的><结果>"`

### 3.2 向量数据库

采用 **Chroma** 向量数据库，存储结构：

- **6 个槽位子空间**（64 维/槽位）：`slot_time`, `slot_subject`, `slot_action`, `slot_object`, `slot_purpose`, `slot_result`
- **1 个全量空间**（384 维）：`full_vectors`

### 3.3 生命周期

- `lifecycle`：记忆的生命周期（秒）
- `LIFECYCLE_INFINITY`：永久记忆（值为 -1）

---

## 4. MQL 查询语言

### 4.1 基本语法

```sql
-- SELECT 查询
SELECT <字段> FROM memories WHERE <条件>

-- INSERT 插入
INSERT INTO memories VALUES (<查询句>, <内容>, <生命周期>)

-- UPDATE 更新
UPDATE memories SET <字段>=<值>,... WHERE <条件>

-- DELETE 删除
DELETE FROM memories WHERE <条件>
```

### 4.2 字段

| 字段类型 | 可用字段 |
|----------|----------|
| 槽位字段 | `time`, `subject`, `action`, `object`, `purpose`, `result` |
| 元数据字段 | `id`, `content`, `lifecycle`, `created_at`, `updated_at` |
| 通配符 | `*`（所有字段） |

### 4.3 条件操作符

| 操作符 | 说明 | 示例 |
|--------|------|------|
| `=` | 等于 | `subject='我'` |
| `!=` | 不等于 | `lifecycle!=0` |
| `<`, `>`, `<=`, `>=` | 比较 | `lifecycle>3600` |
| `LIKE` | 模糊匹配 | `subject LIKE '%学%'` |
| `IN` | 在列表中 | `lifecycle IN [3600, 86400]` |
| `AND`, `OR` | 逻辑组合 | `subject='我' AND action='学'` |

### 4.4 向量搜索

```sql
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10
```

- 槽位放在方括号 `[]` 内
- `SEARCH` 关键字触发向量搜索
- `TOPK n` 指定返回数量

### 4.5 LIMIT

```sql
SELECT * FROM memories WHERE subject='我' LIMIT 10
```

### 4.6 版本控制

```sql
-- 查询历史版本
SELECT * FROM memories WHERE id='mem_xxx' VERSION 1
```

### 4.7 系统管理语句

```sql
-- 创建记忆系统
CREATE DATABASE system_name

-- 删除记忆系统
DROP DATABASE system_name

-- 列出所有系统
LIST DATABASES

-- 切换当前系统
USE system_name
```

### 4.8 用户管理语句

```sql
-- 创建用户
CREATE USER username

-- 删除用户
DROP USER username

-- 列出用户
LIST USERS

-- 授予权限
GRANT <permission> ON <system> TO <username>

-- 撤销权限
REVOKE <permission> ON <system> FROM <username>

-- 生成 APIKey
GENERATE KEY FOR <username>
```

---

## 5. Python API

### 5.1 PyAPI（多数据库管理）

```python
from xcmemory_interest import PyAPI

# 初始化
api = PyAPI("./data/xcmemory")

# 创建记忆系统
system = api.create_system("my_memory", enable_interest_mode=False)

# 列出所有系统
systems = api.list_systems()

# 切换活跃系统
api.set_active_system("my_memory")

# 执行 MQL
result = api.execute("SELECT * FROM memories LIMIT 5")

# 删除系统
api.delete_system("my_memory")
```

### 5.2 MemorySystem（单系统操作）

```python
# 获取系统
system = api.get_system("my_memory")

# 插入记忆
system.execute("""
    INSERT INTO memories VALUES (
        '<平时><我><看><书><为了><增长知识>',
        '我今天看了《百年孤独》',
        86400
    )
""")

# 向量搜索
result = system.execute("""
    SELECT * FROM memories
    WHERE [subject='我', action='看']
    SEARCH TOPK 5
""")

# 条件查询
result = system.execute("""
    SELECT * FROM memories
    WHERE subject='我' AND lifecycle > 3600
    LIMIT 10
""")

# 更新
system.execute("UPDATE memories SET content='新内容' WHERE id='mem_xxx'")

# 删除
system.execute("DELETE FROM memories WHERE lifecycle < 0")
```

### 5.3 用户与鉴权

```python
from xcmemory_interest.user_manager import UserManager, PermissionType

# 初始化用户管理器
um = UserManager("./data/xcmemory")

# 获取管理员 APIKey（初始密码为空）
admin_key = um.generate_api_key("admin")

# 创建用户
um.create_user("alice")

# 授予权限
um.grant_permission("alice", "my_memory", PermissionType.READ_WRITE)

# 获取用户 APIKey
alice_key = um.generate_api_key("alice")

# 验证 APIKey
auth = um.authenticate("xi-alice-" + alice_key)
print(f"用户: {auth.username}, 系统: {auth.system}")
```

### 5.4 权限类型

| 权限 | 说明 |
|------|------|
| `read` | 只读 |
| `write` | 只写 |
| `read_write` | 读写 |
| `version_commit` | 版本提交 |
| `version_delete` | 版本删除 |
| `admin` | 用户管理（仅超级管理员） |

---

## 6. HTTP API

### 6.1 启动服务

```python
from xcmemory_interest.netapi import APIServer

server = APIServer(
    database_root="./data/xcmemory",
    host="0.0.0.0",
    port=8080,
    ws_port=8081,
)
server.start()  # 阻塞启动
```

### 6.2 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/v1/query` | 执行 MQL |
| GET | `/api/v1/systems` | 列出系统 |
| POST | `/api/v1/systems` | 创建系统 |
| GET | `/api/v1/systems/<name>` | 获取系统信息 |
| DELETE | `/api/v1/systems/<name>` | 删除系统 |
| POST | `/api/v1/systems/<name>/use` | 切换系统 |
| GET | `/api/v1/users` | 列出用户 |
| POST | `/api/v1/users` | 创建用户 |
| GET | `/api/v1/users/<username>` | 获取用户信息 |
| DELETE | `/api/v1/users/<username>` | 删除用户 |
| POST | `/api/v1/users/<username>/generate_key` | 生成 APIKey |
| POST | `/api/v1/permissions` | 授予权限 |
| DELETE | `/api/v1/permissions` | 撤销权限 |

### 6.3 认证

所有请求通过 `X-API-Key` Header 传递：

```
X-API-Key: xi-alice-abc123...
```

### 6.4 请求示例

```bash
# 执行 MQL
curl -X POST http://localhost:8080/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"mql": "SELECT * FROM memories LIMIT 5"}'

# 多行 MQL
curl -X POST http://localhost:8080/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"script": "CREATE DATABASE test; USE test; SELECT * FROM memories;"}'

# 创建用户
curl -X POST http://localhost:8080/api/v1/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"username": "alice"}'

# 授予权限
curl -X POST http://localhost:8080/api/v1/permissions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"username": "alice", "system": "my_memory", "permission": "read_write"}'
```

---

## 7. WebSocket API

### 7.1 连接

```
ws://localhost:8081/ws
```

### 7.2 消息格式

```javascript
// 1. 认证
ws.send(JSON.stringify({
    type: 'auth',
    api_key: 'xi-admin-xxx'
}));

// 2. 执行 MQL
ws.send(JSON.stringify({
    type: 'mql',  // 或 'query'
    mql: 'SELECT * FROM memories LIMIT 5'
}));

// 3. 多行 MQL
ws.send(JSON.stringify({
    type: 'mql',
    script: `
        CREATE DATABASE my_system;
        USE my_system;
        SELECT * FROM memories LIMIT 3;
    `
}));

// 4. 切换系统
ws.send(JSON.stringify({
    type: 'use',
    system: 'my_system'
}));

// 5. 心跳
ws.send(JSON.stringify({
    type: 'ping'
}));
```

### 7.3 响应格式

```javascript
// 认证成功
{type: 'auth', success: true, username: 'admin'}

// 查询结果
{
    type: 'result',
    success: true,
    data: [...],
    affected_rows: 1,
    memory_ids: ['mem_xxx'],
    message: 'OK'
}

// 多行结果
{
    type: 'result',
    success: true,
    data: [...],
    script_results: [
        {type: 'result', data: [], affected: 0, message: 'Created'},
        {type: 'result', data: [...], affected: 0, message: 'OK'}
    ]
}
```

---

## 8. 常见问题

### Q1: 如何创建支持兴趣模型的记忆系统？

**当前版本不支持兴趣模型**，创建时会报错：

```python
# 这会抛出 NotImplementedError
system = api.create_system("my_memory", enable_interest_mode=True)
```

### Q2: 如何查看所有记忆系统？

```python
systems = api.list_systems()
print(systems)  # ['system_a', 'system_b', ...]
```

### Q3: 如何备份记忆数据？

记忆数据存储在 `./data/xcmemory/<system_name>/` 目录下，复制整个目录即可：

```bash
cp -r ./data/xcmemory/my_memory ./backup/my_memory
```

### Q4: 如何查看用户权限？

```python
um = UserManager("./data/xcmemory")
perms = um.list_permissions("alice")
for p in perms:
    print(f"{p.system}: {p.permission}")
```

### Q5: 忘记 admin 密码怎么办？

```python
# 重置 admin 的 APIKey
um = UserManager("./data/xcmemory")
new_key = um.reset_api_key("admin")
print(f"新 APIKey: {new_key}")
```
