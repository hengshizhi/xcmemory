# API Auth & 数据库管理设计

> 管理人：XC Memory Team
> 状态：✅ 已实现（v0.4.0）
> 优先级：P1

## 1. 概述

### 1.1 核心需求

在记忆系统之上增加 API 鉴权层和多数据库支持：

- **记忆数据库**：一个根目录下可管理多个记忆系统
- **APIKey 认证**：使用 `xi-<用户名>-<APIKey>` 格式
- **用户权限管理**：记忆系统级别的读写权限 + 版本控制权限
- **超级管理员**：拥有所有权限和用户管理权限
- **MQL 扩展**：所有管理操作都可通过 MQL 语句执行

### 1.2 架构

```
<database_root>/
├── auth.db                 # 用户和权限数据库（SQLite）
└── <system_name>/          # 各记忆系统目录
    ├── vec_db/
    ├── aux_db/
    └── ...

# APIKey 格式
xi-<username>-<api_key>
例：xi-alice-abc123... -> hash(api_key) 存储

# 认证流程
APIKey -> hash -> 对比数据库中存储的 hash
```

## 2. 用户与权限

### 2.1 用户类型

| 用户类型 | 说明 | 权限 |
|----------|------|------|
| 超级管理员 (admin) | 默认创建 | 拥有所有权限，包括用户管理 |
| 普通用户 | 需管理员创建 | 由管理员授予特定系统权限 |

### 2.2 权限类型

| 权限 | 值 | 说明 |
|------|-----|------|
| read | `read` | 只读权限 |
| write | `write` | 只写权限 |
| read_write | `read_write` | 读写权限 |
| version_commit | `version_commit` | 版本控制提交 |
| version_delete | `version_delete` | 版本删除 |
| admin | `admin` | 用户系统管理（仅超级管理员） |

### 2.3 权限范围

- **系统级别**：用户对特定记忆系统的权限
- **全局权限**：admin 权限可操作所有系统和用户

## 3. 数据库管理

### 3.1 目录结构

```
<database_root>/
├── auth.db                    # 用户和权限
├── systems_meta.json          # 系统元数据
├── system_a/
│   ├── vec_db/
│   └── aux_db/
├── system_b/
│   └── ...
└── ...
```

### 3.2 MQL 系统管理语句

```sql
-- 创建记忆系统
CREATE DATABASE system_name

-- 删除记忆系统（仅管理员）
DROP DATABASE system_name

-- 列出所有记忆系统
LIST DATABASES

-- 切换当前系统
USE system_name
```

## 4. 用户管理 MQL 语句

```sql
-- 创建用户（仅管理员）
CREATE USER username

-- 删除用户（仅管理员）
DROP USER username

-- 列出用户（仅管理员）
LIST USERS

-- 授予权限（仅管理员）
GRANT <permission> ON <system> TO <username>
-- 例：GRANT read ON system_a TO alice
--     GRANT read_write ON system_b TO bob

-- 撤销权限（仅管理员）
REVOKE <permission> ON <system> FROM <username>

-- 生成新 APIKey
GENERATE KEY FOR <username>
```

## 5. HTTP API

### 5.1 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/query` | 执行 MQL 语句 |
| GET | `/api/v1/systems` | 列出记忆系统 |
| POST | `/api/v1/systems` | 创建记忆系统 |
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
| GET | `/health` | 健康检查 |

### 5.2 认证

所有请求需要通过 `X-API-Key` header 传递 APIKey：

```
X-API-Key: xi-alice-abc123...
```

### 5.3 请求示例

```bash
# 执行 MQL
curl -X POST http://localhost:8080/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"sql": "SELECT * FROM memories WHERE subject=\"我\" LIMIT 5"}'

# 列出系统
curl http://localhost:8080/api/v1/systems \
  -H "X-API-Key: xi-alice-xxx"

# 创建用户
curl -X POST http://localhost:8080/api/v1/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"username": "alice"}'

# 授予权限
curl -X POST http://localhost:8080/api/v1/permissions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxx" \
  -d '{"username": "alice", "system": "my_system", "permission": "read"}'
```

## 6. 核心文件

| 文件 | 职责 |
|------|------|
| `user_manager.py` | 用户/鉴权/权限管理核心模块 |
| `netapi/__init__.py` | HTTP API Server |
| `mql/interpreter.py` | MQL 解释器（含权限检查） |
| `mql/parser.py` | MQL 语法解析器（含系统/用户语句） |

## 7. 使用示例

### 7.1 Python API

```python
from xcmemory_interest import PyAPI
from xcmemory_interest.user_manager import UserManager, PermissionType

# 初始化
api = PyAPI("./data")
um = UserManager("./data")

# 管理员操作
admin_key = um.generate_api_key("admin")

# 创建用户并授权
um.create_user("alice")
um.grant_permission("alice", "my_system", PermissionType.READ)

# 普通用户操作
alice_key = um.generate_api_key("alice")
# alice 可以读取 my_system
result = api.execute("SELECT * FROM memories WHERE subject='我'")

# MQL 管理
api.execute("CREATE DATABASE new_system")
api.execute("GRANT read_write ON new_system TO alice")
```

### 7.2 HTTP API

```python
from xcmemory_interest.netapi import APIServer

server = APIServer(
    database_root="./data",
    host="0.0.0.0",
    port=8080,
)
server.start()  # 阻塞启动
```