# 星尘记忆系统 (xcmemory_interest)

> v0.4.0 - 结构化记忆检索系统

## 概述

星尘记忆系统是一个基于向量检索的结构化记忆管理系统，支持 6 槽位记忆结构、MQL 查询语言、多数据库管理和 API 鉴权。

**核心特性：**

- **6 槽位记忆结构**：时间、主体、动作、宾语、目的、结果
- **向量搜索**：基于语义相似度的记忆检索（Chroma）
- **生命周期管理**：自动过期和衰减
- **多数据库支持**：一个根目录管理多个独立记忆系统
- **MQL 查询语言**：类 SQL 的字符串查询语法
- **API 鉴权**：用户级别权限控制（HTTP + WebSocket）

## 快速开始

### 安装

```bash
cd o:/project/xcmemory_interest
o:/project/xcmemory_interest/venv/Scripts/pip.exe install -e .
```

### Python API

```python
from xcmemory_interest import PyAPI

# 初始化
api = PyAPI("./data/xcmemory")

# 创建记忆系统
system = api.create_system("my_memory")

# 插入记忆
system.execute("""
    INSERT INTO memories VALUES (
        '<平时><我><学><编程><喜欢><有收获>',
        '我喜欢学编程',
        86400
    )
""")

# 查询记忆
result = system.execute("SELECT * FROM memories LIMIT 5")
for row in result.data:
    print(row)
```

### HTTP API

```python
from xcmemory_interest.netapi import APIServer

server = APIServer(
    database_root="./data/xcmemory",
    host="0.0.0.0",
    port=8080,
    ws_port=8081,
)
server.start()
```

## 文档

| 文档 | 说明 |
|------|------|
| [使用文档](docs/USER_GUIDE.md) | 完整使用指南 |
| [开发文档](docs/DEVELOPER_GUIDE.md) | 架构设计和扩展指南 |
| [API 参考](docs/API_REFERENCE.md) | 完整 API 文档 |

## 项目结构

```
xcmemory_interest/
├── src/xcmemory_interest/   # 源码包
│   ├── __init__.py
│   ├── config.py            # 配置
│   ├── user_manager.py      # 用户管理
│   ├── basic_crud/          # 向量数据库 CRUD
│   ├── vector_db/           # Chroma 向量数据库封装
│   ├── embedding_coder/     # 查询句嵌入编码（InterestEncoder）
│   ├── auxiliary_query/    # 辅助查询（索引 + 调度）
│   ├── lifecycle_manager/   # 生命周期管理
│   ├── version_control/     # 版本控制
│   ├── mql/                 # MQL 查询语言
│   ├── pyapi/               # Python 应用层封装
│   ├── netapi/              # HTTP/WS API
│   ├── graph_query/         # 图查询
│   ├── online_learning/     # 在线学习
│   └── docs/                # 文档
│       ├── USER_GUIDE.md
│       ├── DEVELOPER_GUIDE.md
│       └── API_REFERENCE.md
├── venv/                    # Python 虚拟环境
├── pyproject.toml
└── README.md
```

## MQL 示例

```sql
-- 插入
INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)

-- 向量搜索
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 5

-- 条件查询
SELECT * FROM memories WHERE subject='我' AND lifecycle > 3600 LIMIT 10

-- 更新
UPDATE memories SET content='新内容' WHERE id='mem_xxx'

-- 删除
DELETE FROM memories WHERE lifecycle < 0

-- 系统管理
CREATE DATABASE new_system
USE new_system
LIST DATABASES

-- 用户管理
CREATE USER alice
GRANT read_write ON my_memory TO alice
```

## 版本历史

| 版本 | 说明 |
|------|------|
| v0.4.0 | MQL 解释器、API 鉴权、多数据库管理、HTTP/WS 支持 |
