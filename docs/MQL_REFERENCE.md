# MQL 大全 (Memory Query Language)

> 星尘记忆系统查询语言，类 SQL 风格，操作记忆的 DSL。

---

## 一、核心概念

### 1.1 查询句格式

所有记忆的 query_sentence 必须为 6 槽格式：

```
<scene><subject><action><object><purpose><result>
```

示例：`<平时><我><学习><Python><提升><成长>`

### 1.2 字段体系

| 类型 | 字段 | 说明 |
|------|------|------|
| 槽位字段 | `scene`, `subject`, `action`, `object`, `purpose`, `result` | 从 query_sentence 解析 |
| 元数据字段 | `id`, `content`, `lifecycle`, `created_at`, `updated_at` | 直接访问 |
| 通配符 | `*` / `ALL` | 所有字段 |

### 1.3 生命周期常量

| 常量 | 值 | 说明 |
|------|---|------|
| `LIFECYCLE_INFINITY` | 999999 | 永不过期 |
| `SHORT_TERM_CAP` | 604800 (7天) | 短期记忆上限 |
| `LONG_TERM_CAP` | 2592000 (30天) | 长期记忆上限 |
| `TRANSITION_CAP` | 31536000 (365天) | 跃迁临界值 |

---

## 二、CRUD 语句

### 2.1 SELECT — 查询记忆

**语法：**
```sql
SELECT <字段> FROM memories
[WHERE <条件> | WHERE [<槽位>=<值>,...] SEARCH [TOPK <数量>]]
[VERSION <版本号>]
[GRAPH <操作>(<参数>)]
[TIME year(...) AND month(...) AND day(...) AND clock(...)]
[TOPK <数量>]
[LIMIT <数量>]
```

> **TIME / TOPK / LIMIT 按书写顺序执行**，例如 `TIME ... TOPK ... LIMIT ...` 表示先时间过滤、再匹配度排序、最后截断。

**可用字段：** `*`、`id`、`content`、`scene`、`subject`、`action`、`object`、`purpose`、`result`、`lifecycle`、`created_at`、`updated_at`

**条件操作符：** `=` / `==`（等于）、`!=`（不等于）、`<`、`>`、`<=`、`>=`、`LIKE`、`IN`

**逻辑组合：** `AND`、`OR`

**示例：**

```sql
-- 查询全部（默认最多200条）
SELECT * FROM memories

-- 精确匹配单槽
SELECT * FROM memories WHERE subject='我'

-- 多条件组合
SELECT * FROM memories WHERE subject='我' AND action='学习'

-- 模糊匹配
SELECT * FROM memories WHERE content LIKE '%Python%'
SELECT * FROM memories WHERE subject LIKE '%学%'

-- IN 列表
SELECT * FROM memories WHERE lifecycle IN [3600, 86400, 604800]

-- 限制返回数量
SELECT id, content, subject FROM memories WHERE subject='我' LIMIT 10

-- 查询特定 ID
SELECT * FROM memories WHERE id='mem_abc123'

-- 按生命周期筛选
SELECT * FROM memories WHERE lifecycle > 86400
SELECT * FROM memories WHERE lifecycle >= 999999  -- 永不过期的记忆
SELECT * FROM memories WHERE lifecycle < 604800   -- 短期记忆

-- 时间排序
SELECT * FROM memories ORDER BY created_at DESC LIMIT 50
```

**⚠️ WHERE 条件中的槽位字段（scene/subject/action/object/purpose/result）**
> 条件中的槽位字段**必须从 query_sentence 解析**，值是 `<...>` 中的原始字符串，不是字段名本身。
> 正确：`WHERE subject='我'`（"我" 是 query_sentence 解析出来的值）
> 错误：`WHERE subject='subject'`（误把槽位名当值）

---

### 2.2 INSERT — 写入记忆

**语法：**
```sql
INSERT INTO memories VALUES ('<query_sentence>', '<content>', <lifecycle>)
```

**参数说明：**

| 参数 | 必需 | 说明 |
|------|------|------|
| query_sentence | ✅ | 6槽格式字符串 |
| content | 否 | 记忆内容，默认空字符串 |
| lifecycle | 否 | 参考生命周期（秒），省略/NULL 则由 LifecycleManager 决策（默认86400） |

**示例：**

```sql
-- 标准写入（3个参数）
INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)

-- 仅传 query_sentence（content 默认为空，lifecycle 由系统决定）
INSERT INTO memories VALUES ('<周末><我><跑步><锻炼><健康><坚持>', '', 3600)

-- 省略 lifecycle（使用默认参考值 86400）
INSERT INTO memories VALUES ('<平时><我><写作><博客><分享><交流>', 'Write blog', NULL)
```

---

### 2.3 UPDATE — 更新记忆

**语法：**
```sql
UPDATE memories SET <field>=<value>[, <field>=<value>...]
WHERE <条件>
```

**可更新字段：** `content`、`lifecycle`

**示例：**

```sql
-- 更新内容
UPDATE memories SET content='新内容' WHERE id='mem_abc123'

-- 同时更新内容和生命周期
UPDATE memories SET content='更新后的内容', lifecycle=604800 WHERE id='mem_abc123'

-- 按条件批量更新
UPDATE memories SET lifecycle=86400 WHERE subject='我'

-- 按多条件更新
UPDATE memories SET content='重要更新', lifecycle=999999 WHERE subject='我' AND action='学习'
```

---

### 2.4 DELETE — 删除记忆

**语法：**
```sql
DELETE FROM memories WHERE <条件>
```

**示例：**

```sql
-- 删除指定记忆
DELETE FROM memories WHERE id='mem_abc123'

-- 按条件删除
DELETE FROM memories WHERE lifecycle < 3600
DELETE FROM memories WHERE subject='测试'

-- 组合条件删除
DELETE FROM memories WHERE subject='我' AND action='打'
```

---

## 三、向量搜索

### 3.1 子空间搜索（Subspace Search）

**原理：** 在每个查询槽位的独立 64 维 Collection 中分别搜索 → 取候选集交集 → 按槽位匹配数+距离排序。

**语法：**
```sql
SELECT * FROM memories WHERE [<槽位>=<值>[, <槽位>=<值>]...] SEARCH TOPK <数量>
```

**规则：**
- 槽位放在方括号 `[]` 内，多个槽位用逗号分隔（AND 关系）
- `SEARCH` 关键字触发向量搜索
- `TOPK <n>` 指定返回结果数量（默认5）
- 支持槽位：`scene`、`subject`、`action`、`object`、`purpose`、`result`

**示例：**

```sql
-- 单槽位搜索
SELECT * FROM memories WHERE [subject='我'] SEARCH TOPK 5

-- 多槽位搜索（AND 关系）
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10

-- 全六槽搜索
SELECT * FROM memories WHERE [scene='平时', subject='我', action='学', object='编程', purpose='喜欢', result='有收获'] SEARCH TOPK 5

-- 搜索更多结果
SELECT * FROM memories WHERE [purpose='锻炼身体'] SEARCH TOPK 20

-- 结合 WHERE 条件过滤（先搜索后过滤）
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10

-- 等价写法：条件 + SEARCH
SELECT * FROM memories WHERE subject='我' SEARCH TOPK 10
```

### 3.2 全空间搜索（Fullspace Search）

**原理：** 在 384 维完整向量 Collection 中搜索，支持槽位匹配数+距离重排序。

**语法：**
```sql
SELECT * FROM memories WHERE [<槽位>=<值>[, <槽位>=<值>]...] SEARCH TOPK <数量> FULLSPACE
```

**与子空间搜索的区别：**

| 对比项 | 子空间搜索 | 全空间搜索 |
|--------|-----------|-----------|
| 向量空间 | 各槽位独立 64 维 | 完整 384 维 |
| 搜索方式 | 多 Collection 交集 | 单 Collection 搜索 |
| 适用场景 | 槽位明确的精准过滤 | 语义相似度的模糊匹配 |

**示例：**

```sql
-- 全空间向量搜索
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10 FULLSPACE

-- 全空间单槽搜索
SELECT * FROM memories WHERE [purpose='成长'] SEARCH TOPK 5 FULLSPACE
```

---

## 四、时间过滤（TIME）

### 4.1 语法

`TIME` 关键字用于按记忆的创建时间（`created_at`）进行过滤，支持 **year/month/day/clock** 四个维度独立限定，用 `AND` 连接。

```sql
TIME [year(Y [TO Y | *]) [AND month(M [TO M | *]) [AND day(D [TO D | *]) [AND clock(HH:MM [TO HH:MM | *])]]]]
```

### 4.2 四个维度

| 维度 | 格式 | 说明 | 默认值 |
|------|------|------|--------|
| `year` | `year(2024)` / `year(2024 TO 2025)` / `year(*)` | 年份范围 | `*`（不过滤） |
| `month` | `month(03)` / `month(01 TO 06)` / `month(*)` | 月份范围（1-12） | `*` |
| `day` | `day(15)` / `day(01 TO 15)` / `day(*)` | 日期范围（1-31） | `*` |
| `clock` | `clock(09:00 TO 18:00)` / `clock(*)` | 日内时段（24h） | `*` |

**规则：**
- 各维度用 `AND` 连接，**全部指定维度必须同时满足**（AND 关系）
- 不写的维度 = `*`（不过滤）
- 不写 `TIME` = 不做时间过滤
- `year(2024)` 等价于 `year(2024 TO 2024)`
- `OR` 支持但当前只取第一个值

### 4.3 示例

```sql
-- 查询 2024 年的记忆
SELECT * FROM memories TIME year(2024)

-- 查询 2024-2025 年的记忆
SELECT * FROM memories TIME year(2024 TO 2025)

-- 查询每年 1-3 月的记忆
SELECT * FROM memories TIME year(*) AND month(01 TO 03)

-- 查询 2024 年 1-3 月的记忆
SELECT * FROM memories TIME year(2024) AND month(01 TO 03)

-- 查询每天 9:00-18:00 的记忆
SELECT * FROM memories TIME clock(09:00 TO 18:00)

-- 查询 2024 年每月 21-30 号的 12:30-20:00 时段记忆
SELECT * FROM memories TIME year(2024) AND day(21 TO 30) AND clock(12:30 TO 20:00)

-- 结合 WHERE 条件
SELECT * FROM memories WHERE subject='我' TIME year(2025) AND month(01 TO 03) LIMIT 10

-- 结合向量搜索
SELECT * FROM memories WHERE [subject='我'] SEARCH TOPK 20 TIME year(2024)
```

### 4.4 执行顺序

`TIME`、`TOPK`、`LIMIT` 三个修饰符**按书写顺序从左到右执行**：

```sql
-- 先时间过滤，后匹配度排序
SELECT * FROM memories TIME year(2024) TOPK 5

-- 先匹配度排序，后时间过滤
SELECT * FROM memories TOPK 5 TIME year(2024)

-- 先时间过滤，再排序，最后截断
SELECT * FROM memories TIME year(2024) TOPK 10 LIMIT 5
```

**完整执行顺序**：`WHERE → VERSION → GRAPH → ops(TIME/TOPK/LIMIT 按书写顺序)`

---

## 四、版本控制

**语法：**
```sql
SELECT * FROM memories WHERE id='<memory_id>' VERSION <版本号>
```

**版本号格式：** 支持 `VERSION 1` 或 `VERSION v1`

**示例：**

```sql
-- 查询记忆的第一个历史版本
SELECT * FROM memories WHERE id='mem_abc123' VERSION 1

-- 查询第三个版本
SELECT * FROM memories WHERE id='mem_abc123' VERSION 3
```

---

## 五、系统管理

### 5.1 创建记忆系统

```sql
CREATE DATABASE <系统名>
```

```sql
-- 示例
CREATE DATABASE my_system
CREATE DATABASE work_memory
```

### 5.2 删除记忆系统

```sql
DROP DATABASE <系统名>
```

```sql
DROP DATABASE test_system
```

### 5.3 列出所有记忆系统

```sql
LIST DATABASES
-- 或
LIST SYSTEMS
```

### 5.4 切换活跃系统

```sql
USE <系统名>
```

```sql
USE my_system
```

---

## 六、用户管理

### 6.1 创建用户

```sql
CREATE USER <用户名>
```

```sql
CREATE USER alice
CREATE USER bob
```

### 6.2 删除用户

```sql
DROP USER <用户名>
```

```sql
DROP USER alice
```

### 6.3 列出所有用户

```sql
LIST USERS
```

### 6.4 授予权限

```sql
GRANT <权限类型> ON <系统名> TO <用户名>
```

**权限类型：** `read`、`write`、`admin`

```sql
-- 授予读权限
GRANT read ON my_system TO alice

-- 授予读写权限
GRANT write ON my_system TO bob

-- 授予管理员权限
GRANT admin ON * TO admin_user
```

### 6.5 撤销权限

```sql
REVOKE <权限类型> ON <系统名> FROM <用户名>
```

```sql
REVOKE write ON my_system FROM alice
REVOKE read ON work_memory FROM bob
```

### 6.6 生成用户 APIKey

```sql
GENERATE KEY FOR <用户名>
```

```sql
GENERATE KEY FOR alice
```

---

## 七、特殊语法

### 7.1 LIMIT 子句

```sql
-- 限制返回数量
SELECT * FROM memories LIMIT 10
SELECT * FROM memories WHERE subject='我' LIMIT 5
```

### 7.2 TOPK 子句

过滤后按向量匹配度排序取前 k 条。与 `SEARCH TOPK` 不同，独立 `TOPK` 作用于已过滤结果。

```sql
-- 先时间过滤，再按匹配度取前 5
SELECT * FROM memories WHERE subject='我' TIME year(2024) TOPK 5

-- 先匹配度排序取前 10，再时间过滤
SELECT * FROM memories WHERE subject='我' TOPK 10 TIME year(2024)
```

### 7.3 字符串引号

- 单引号 `'value'`
- 双引号 `"value"`
- 引号在条件值两边必须成对，查询句 `<...>` 中的内容可含空格和中文

### 7.3 空值

```sql
-- 匹配空 content
SELECT * FROM memories WHERE content=NULL

-- 在 INSERT 中省略可选参数
INSERT INTO memories VALUES ('<平时><我><写><代码><工作><完成>', '', NULL)
```

---

## 八、执行方式

### 8.1 HTTP API 调用

**基础 URL：** `http://localhost:8080`

**认证：** Header 中添加 `X-API-Key: <your_key>`

**单条执行：**
```bash
curl -X POST http://localhost:8080/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxxx" \
  -d '{"sql": "SELECT * FROM memories WHERE subject='"'"'我'"'"' LIMIT 5"}'
```

**多条脚本（分号分隔）：**
```bash
curl -X POST http://localhost:8080/api/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: xi-admin-xxxx" \
  -d '{"sql": "SELECT * FROM memories LIMIT 3; INSERT INTO memories VALUES ('"'"'<平时><我><测试><MQL><验证><成功>'"'"', '"'"'test'"'"', 86400)"}'
```

### 8.2 Python API 调用

```python
from xcmemory_interest.mql import Interpreter

inter = Interpreter()
inter.bind("mem", memory_system)   # 绑定记忆系统
inter.bind("api", pyapi)            # 绑定 PyAPI
inter.bind("um", user_manager)       # 绑定用户管理器

# 单条执行
result = inter.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")
print(f"查到 {result.affected_rows} 条")
for row in result.data:
    print(row)

# 多条脚本
results = inter.execute_script("""
    SELECT * FROM memories LIMIT 3;
    INSERT INTO memories VALUES ('<平时><我><测试><MQL><验证><成功>', 'test', 86400);
""")
for r in results:
    print(r.affected_rows, r.message)
```

### 8.3 Gradio WebUI 调用

在 MQL 查询 Tab 中直接输入语句，点击执行即可。

---

## 九、完整示例

### 9.1 日常记忆管理

```sql
-- 记录一条学习笔记
INSERT INTO memories VALUES ('<平时><我><学习><Python><提升技能><成长>', '今天学了装饰器 decorators', 86400)

-- 查询所有学习相关记忆
SELECT * FROM memories WHERE [action='学习'] SEARCH TOPK 10

-- 更新为重要内容
UPDATE memories SET content='⭐ 重点：装饰器是 Python 的高级特性', lifecycle=604800 WHERE id='mem_abc123'

-- 删除测试记忆
DELETE FROM memories WHERE id='mem_test123'
```

### 9.2 多系统管理

```sql
-- 创建独立系统
CREATE DATABASE work_memory

-- 切换到工作记忆系统
USE work_memory

-- 写入工作记忆
INSERT INTO memories VALUES ('<周会><团队><讨论><架构><决策><方向>', '决定使用微服务架构', 86400)

-- 列出所有系统
LIST DATABASES

-- 切换回主系统
USE default
```

### 9.3 用户与权限

```sql
-- 创建新用户
CREATE USER alice

-- 授予权限
GRANT read ON work_memory TO alice
GRANT write ON work_memory TO alice

-- 撤销权限
REVOKE write ON work_memory FROM alice

-- 列出所有用户
LIST USERS
```

### 9.4 向量搜索实战

```sql
-- 场景：查找所有"我学习编程"相关的记忆
SELECT * FROM memories WHERE [subject='我', action='学习', object='编程'] SEARCH TOPK 10

-- 场景：查找"锻炼身体"相关的记忆
SELECT * FROM memories WHERE [purpose='锻炼身体'] SEARCH TOPK 10 FULLSPACE

-- 场景：查找特定时间的记忆
SELECT * FROM memories WHERE [scene='周末'] SEARCH TOPK 5

-- 场景：结合条件过滤
SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10
```

---

## 十、注意事项

1. **查询句格式严格**：`query_sentence` 必须是 6 个 `<...>` 拼接的格式，少一个或多一个都会导致解析错误。

2. **中文字符**：确保输入输出编码为 UTF-8。

3. **向量搜索依赖**：子空间和全空间搜索需要 InterestEncoder 初始化。

4. **生命周期单位**：所有 lifecycle 值以**秒**为单位。

5. **MQL 不支持 JOIN**：需要跨记忆关联时，通过多次查询实现。

6. **权限控制**：非管理员用户只能访问已授权的系统，操作前需确认权限。

7. **版本控制**：需要 VersionManager 初始化，否则 VERSION 子句无效。

---

## 图查询（GRAPH）

`GRAPH` 是 MQL 的图扩展关键字，可对 SELECT 结果执行多跳关联扩展。

### 语法

```sql
SELECT * FROM memories [WHERE ...] GRAPH <operation>(<params>)
```

### 支持的操作

| 操作 | 说明 | 典型用法 |
|------|------|----------|
| `EXPAND(HOPS n)` | 从起始记忆出发，扩展 n 跳邻居 | 多跳探索 |
| `NEIGHBORS(MIN_SHARED m)` | 获取直接相邻的记忆（共享 m 个以上槽位） | 找直接关联 |
| `PATH(TO 'id' MIN_SHARED m)` | 查找到目标记忆的路径 | 路径发现 |
| `CONNECTED(MIN_SHARED m)` | 获取所有连通记忆（不限跳数） | 整体关联 |
| `VALUE_CHAIN(SLOTS [slot1,slot2,...])` | 沿槽位值链扩展搜索 | 值链追踪 |

### 参数格式

参数支持两种写法（等号可省略）：

```sql
GRAPH EXPAND(HOPS=2)        -- 带等号
GRAPH EXPAND(HOPS 2)        -- 不带等号
GRAPH PATH(TO 'mem_xxx' MIN_SHARED 1)
```

### 示例

```sql
-- 从"学 Python"的记忆出发，扩展2跳关联
SELECT * FROM memories WHERE [subject='我', action='学习', object='Python'] GRAPH EXPAND(HOPS 2)

-- 获取所有与"学 Python"连通、共享至少2个槽位的记忆
SELECT * FROM memories WHERE [subject='我', action='学习'] GRAPH CONNECTED(MIN_SHARED 2)

-- 查找从记忆 A 到记忆 B 的路径（最多3跳）
SELECT * FROM memories WHERE id='mem_abc' GRAPH PATH(TO 'mem_xyz' HOPS 3)

-- 沿 action 和 object 槽位追踪值链
SELECT * FROM memories WHERE [subject='我'] GRAPH VALUE_CHAIN(SLOTS [action, object])

-- 结合向量搜索和图扩展
SELECT * FROM memories WHERE [purpose='提升技能'] SEARCH TOPK 20 GRAPH EXPAND(HOPS 1)
```

### 实现原理

`GRAPH` 子句在 SELECT 结果（memory_id 列表）的基础上，由 `MemoryGraph` 执行：
1. 从 `slot_value_index` 构建"槽位值 -> 记忆ID"的反向索引
2. 在指定跳数内查找共享槽位值的记忆
3. 返回扩展后的记忆列表

---

## 函数包装（WRAP / DEFINE）

支持将一段 SELECT 查询包装为可复用的"视图"或直接内联执行。

### WRAP 内联包装

`WRAP(...)` 将括号内的 SQL 作为子查询执行，结果直接返回：

```sql
-- 等价于直接执行内部 SELECT
SELECT * FROM memories WRAP(SELECT * FROM memories WHERE subject='我' LIMIT 5)

-- WRAP 可以嵌套在其他语句中
SELECT * FROM memories WRAP(SELECT * FROM memories WHERE [subject='我'] SEARCH TOPK 10) LIMIT 3
```

### DEFINE 命名视图

`DEFINE` 创建命名视图（存储在 Interpreter 实例中）：

```sql
DEFINE my_skills AS SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 10;
DEFINE recent_memories AS SELECT * FROM memories WHERE [subject='我'] LIMIT 20;
```

> 注意：`DEFINE` 的视图存储在 `Interpreter._views` 字典中，作用域为单个 Interpreter 实例。
