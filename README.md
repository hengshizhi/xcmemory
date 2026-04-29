# 星尘记忆系统 (xcmemory_interest)

> v0.4.0 - 结构化记忆检索系统

## 概述

星尘记忆系统是一个基于向量检索的结构化记忆管理系统，支持 6 槽位记忆结构、MQL 查询语言、自然语言查询、角色扮演 Chat 和多数据库管理。

**核心特性：**

- **6 槽位记忆结构**：`<scene><subject><action><object><purpose><result>` 时空场景 × 主体 × 动作 × 宾语 × 目的 × 结果
- **向量搜索**：基于语义相似度的记忆检索（ChromaDB）
- **NL Pipeline**：自然语言 → 意图识别 → MQL 生成 → 执行 → 自然语言回答
- **MQL 查询语言**：自定义 DSL，支持条件查询、图扩展（GRAPH）、时间过滤（TIME）、跨槽位搜索
- **角色扮演 Chat**：独立 Chat 应用，角色卡驱动，记忆自动读写
- **生命周期管理**：自动过期和衰减（LIFECYCLE_INFINITY / 30天 / 7天 / 1天）
- **多数据库支持**：一个根目录管理多个独立记忆系统，用户权限控制
- **HTTP + WebSocket API**：RESTful API 服务，支持鉴权

## 快速开始

### 一键安装

```bash
git clone <repo-url> xcmemory_interest
cd xcmemory_interest
python install.py
```

安装脚本会自动：创建虚拟环境 → 安装核心依赖 → 引导安装 PyTorch → 安装 Chat 依赖。

### 启动服务器

```bash
# 完整启动（HTTP + WebSocket）
venv/Scripts/python.exe start_server.py

# 带 WebUI 管理面板
venv/Scripts/python.exe start_server.py --gradio

# 不安装 PyTorch 时使用
venv/Scripts/python.exe start_server_notorch.py
```

首次启动自动生成 `config.toml`（含 admin API Key）。

### 启动 Chat

```bash
venv/Scripts/python.exe chat/main.py --character example
```

首次启动自动生成 `chat/config.toml`，编辑填入你的 LLM API Key 和记忆服务器地址。

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

### 自然语言查询

```python
from xcmemory_interest.nl import NLPipeline

pipeline = NLPipeline(llm_client, memory_system, model="gpt-4o-mini")
result = await pipeline.run("我之前学 Python 时遇到什么问题来着？", history=[])

print(result["nl_response"])  # 自然语言回答
```

### HTTP API

```bash
# 插入
curl -H "Authorization: Bearer <api_key>" \
     -d '{"mql": "INSERT INTO memories VALUES (''<平时><我><学><Python><技能><Python>'',''我在学Python'',86400)"}' \
     http://127.0.0.1:8080/execute

# 查询
curl -H "Authorization: Bearer <api_key>" \
     -d '{"mql": "SELECT * FROM memories WHERE subject=''我'' LIMIT 5"}' \
     http://127.0.0.1:8080/execute
```

## 项目结构

```
xcmemory_interest/
├── src/xcmemory_interest/   # 核心源码包
│   ├── basic_crud/          # 向量数据库 CRUD
│   ├── vector_db/           # ChromaDB 封装 + 子空间搜索 + 重排序
│   ├── auxiliary_query/     # 辅助查询（索引 + KV/SQL存储 + 调度器）
│   ├── lifecycle_manager/   # 生命周期管理（过期 + 衰减）
│   ├── embedding_coder/     # InterestEncoder 嵌入模型
│   ├── mql/                 # MQL 词法 → 语法 → 解释器
│   ├── pyapi/               # Python API（多系统管理）
│   ├── netapi/              # HTTP + WebSocket API 服务器
│   ├── nl/                  # 自然语言处理管道
│   │   ├── intent_classifier.py    # 意图识别（写入/查询分流）
│   │   ├── mql_generator.py        # NL → SELECT MQL 生成
│   │   ├── write_mql_generator.py  # NL → INSERT MQL 生成
│   │   ├── slot_extractor.py       # 6槽提取
│   │   ├── pipeline.py             # 完整 NL Pipeline 编排
│   │   └── ...
│   ├── prompts/             # LLM 提示词集中管理
│   │   ├── __init__.py
│   │   └── nl.py            # 12 个 NL 模块提示词模板
│   ├── graph_query/         # 图查询（多跳扩展）
│   ├── version_control/     # 记忆版本控制
│   ├── config.py            # 常量配置
│   └── user_manager.py      # 用户认证 + 权限管理
├── chat/                    # 独立 Chat 应用
│   ├── main.py              # 入口
│   ├── chat_engine.py       # 对话引擎（记忆管家 + 扮演 LLM）
│   ├── prompts.py           # Chat 提示词
│   ├── characters/          # 角色卡（YAML）
│   └── ui/                  # 终端 UI
├── tests/                   # 测试
├── docs/                    # 文档
│   ├── USER_GUIDE.md
│   ├── DEVELOPER_GUIDE.md
│   ├── API_REFERENCE.md
│   └── MQL_REFERENCE.md
├── install.py               # 一键安装脚本
├── pyproject.toml           # 项目配置
└── README.md
```

## MQL 快速参考

```sql
-- 插入（六槽 + 内容 + 生命周期秒数）
INSERT INTO memories VALUES ('<平时><我><学><Python><技能><Python>', '学习Python', 86400)

-- 查询（条件 + 向量搜索 + 时间过滤 + 图扩展）
SELECT * FROM memories WHERE subject='我' LIMIT 10
SELECT * FROM memories WHERE [subject='我', purpose='喜欢'] SEARCH TOPK 5
SELECT * FROM memories WHERE subject='我' TIME year(2026) AND month(04)
SELECT * FROM memories WHERE subject='我' GRAPH EXPAND(HOPS 2) LIMIT 20
SELECT * FROM memories WHERE ' Python ' AND '编程' LIMIT 10   -- 跨槽位搜索

-- 更新 / 删除
UPDATE memories SET content='新内容' WHERE id='mem_xxx'
DELETE FROM memories WHERE lifecycle < 0

-- 系统管理
CREATE DATABASE new_system
USE new_system
LIST DATABASES

-- 用户管理
CREATE USER alice
GRANT read_write ON my_memory TO alice
```

## 配置

### 服务器配置 (`config.toml`)

首次启动自动生成，包含：`server`（端口/路径）、`lifecycle_manager`（生命周期参数）、`openai`（LLM 接口）、`admin`（管理员 API Key）、`snapshot`（自动快照）。

### Chat 配置 (`chat/config.toml`)

首次启动自动生成，需手动填入：`llm.api_key`（LLM API Key）、`llm.base_url`、`memory.api_key`（记忆服务器 API Key）。

## 文档

| 文档 | 说明 |
|------|------|
| [使用文档](docs/USER_GUIDE.md) | 完整使用指南 |
| [开发文档](docs/DEVELOPER_GUIDE.md) | 架构设计和扩展指南 |
| [API 参考](docs/API_REFERENCE.md) | HTTP/WS/WebUI API 文档 |
| [MQL 参考](docs/MQL_REFERENCE.md) | MQL 语法参考 |

## 版本历史

| 版本 | 说明 |
|------|------|
| v0.4.0 | NL Pipeline、MQL GRAPH/TIME、Chat 应用、HTTP/WS API、提示词分离 |
