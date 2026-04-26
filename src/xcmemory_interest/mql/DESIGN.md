# MQL - Memory Query Language 设计文档

> 管理人：XC Memory Team
> 状态：✅ 已实现（v0.4.0）
> 优先级：P1

## 1. 概述

MQL（Memory Query Language）是一种类 SQL 的字符串查询语言，用于操作记忆系统。通过简洁的 DSL 语法，用户可以用字符串方式操作记忆，无需编写 Python 代码。

## 2. 语法

### 2.1 基本语句

```sql
-- SELECT 查询
SELECT <fields> FROM memories WHERE <conditions>

-- INSERT 插入
INSERT INTO memories VALUES (<query_sentence>, <content>, <lifecycle>)

-- UPDATE 更新
UPDATE memories SET <field>=<value>,... WHERE <conditions>

-- DELETE 删除
DELETE FROM memories WHERE <conditions>
```

### 2.2 字段

| 字段类型 | 可用字段 |
|----------|----------|
| 槽位字段 | `scene`, `subject`, `action`, `object`, `purpose`, `result` |
| 元数据字段 | `id`, `content`, `lifecycle`, `created_at`, `updated_at` |
| 通配符 | `*`（所有字段） |

### 2.3 条件操作符

| 操作符 | 说明 | 示例 |
|--------|------|------|
| `=` | 等于 | `subject='我'` |
| `!=` | 不等于 | `lifecycle!=0` |
| `<`, `>`, `<=`, `>=` | 比较 | `lifecycle>3600` |
| `LIKE` | 模糊匹配 | `subject LIKE '%学%'` |
| `IN` | 在列表中 | `lifecycle IN [3600, 86400]` |
| `AND`, `OR` | 逻辑组合 | `subject='我' AND action='学'` |

### 2.4 向量搜索

```sql
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10
```

- 槽位放在方括号 `[]` 内
- `SEARCH` 关键字触发向量搜索
- `TOPK n` 指定返回数量

### 2.5 版本控制

```sql
SELECT * FROM memories WHERE id='mem_xxx' VERSION 1
```

- `VERSION n` 查询历史版本

### 2.6 LIMIT

```sql
SELECT * FROM memories WHERE subject='我' LIMIT 10
```

## 3. 使用示例

### 3.1 Python API

```python
from xcmemory_interest import PyAPI

api = PyAPI("./data/xcmemory")
system = api.create_system("test")

# 插入
system.execute(
    "INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)"
)

# 查询
result = system.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")
for row in result.data:
    print(row)

# 向量搜索
result = system.execute(
    "SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 5"
)

# 更新
system.execute("UPDATE memories SET content='新内容' WHERE id='mem_xxx'")

# 删除
system.execute("DELETE FROM memories WHERE lifecycle<3600")
```

### 3.2 查询结果

```python
result = system.execute("SELECT * FROM memories WHERE subject='我'")
print(f"Found: {result.affected_rows}")
print(f"IDs: {result.memory_ids}")
for row in result.data:
    print(row)
```

## 4. 实现架构

```
mql/
├── __init__.py       # 模块入口
├── errors.py         # 错误定义
├── lexer.py         # 词法分析器
├── parser.py        # 语法分析器
└── interpreter.py   # 解释器
```

### 4.1 词法分析器 (lexer.py)

将输入字符串分解为 token 流：

```python
from xcmemory_interest.mql import tokenize

tokens = tokenize("SELECT * FROM memories WHERE subject='我'")
# [Token(SELECT), Token(MUL), Token(FROM), Token(IDENTIFIER), ...]
```

### 4.2 语法分析器 (parser.py)

将 token 流解析为 AST：

```python
from xcmemory_interest.mql import parse

ast = parse("SELECT * FROM memories WHERE subject='我'")
# SelectStatement(fields=['*'], conditions=[Condition(...)], ...)
```

### 4.3 解释器 (interpreter.py)

执行 AST，操作记忆系统：

```python
from xcmemory_interest.mql import Interpreter

inter = Interpreter()
inter.bind("mem", memory_system)
result = inter.execute("SELECT * FROM memories")
```

## 5. 与 PyAPI 集成

MQL 已集成到 `MemorySystem` 和 `PyAPI`：

```python
# MemorySystem
system = api.get_system("test")
result = system.execute("SELECT * FROM memories WHERE subject='我'")

# PyAPI（代理到当前活跃系统）
api.set_active_system("test")
result = api.execute("SELECT * FROM memories")
```

## 6. 错误处理

```python
from xcmemory_interest.mql import MQLError, ParseError, ExecutionError

try:
    result = system.execute("SELECT * FORM memories")  # 拼写错误
except ParseError as e:
    print(f"语法错误: {e}")
except ExecutionError as e:
    print(f"执行错误: {e}")
```

## 7. 限制与注意事项

1. **查询句格式**：必须使用 `<槽位1><槽位2>...<槽位6>` 格式
2. **中文字符**：确保输入编码为 UTF-8
3. **版本控制**：需要 `VersionManager` 已初始化
4. **向量搜索**：需要 `InterestEncoder` 已初始化