# MQL 完整语法规范

Memory Query Language — 星辰记忆系统的查询与操作语言。

---

## 一、总览

MQL 是类 SQL 的领域语言，用于操作记忆系统。支持以下语句类型：

| 类型 | 语句 | 说明 |
|------|------|------|
| **查询** | SELECT | 检索记忆（支持 WHERE/TIME/GRAPH/TOPK/LIMIT） |
| **写入** | INSERT | 新增记忆 |
| **修改** | UPDATE | 修改记忆内容 |
| **删除** | DELETE | 删除记忆（支持 DRYRUN 预览） |
| **图查询** | GRAPH | 多跳关联扩展 |
| **函数** | DEFINE / WRAP | 定义命名查询 / 包装 SQL |
| **系统管理** | CREATE/DROP/LIST/USE | 数据库管理 |
| **用户管理** | CREATE USER / GRANT / REVOKE | 权限控制 |

### 多行执行

用分号 `;` 分隔多条语句，支持批量执行：

```sql
SELECT * FROM memories WHERE subject='星织' LIMIT 5;
INSERT INTO memories VALUES ('<平时><星织><是><温柔><无><无>', '星织很温柔', 999999);
```

### 注释

行注释：`-- 这是注释`

---

## 二、SELECT 查询语法

### 完整语法

```
SELECT <fields> FROM memories
  [WHERE <conditions> | WHERE [slot=value,...] SEARCH [TOPK n]]
  [VERSION vN]
  [GRAPH <graph_operation>]
  [TIME <time_filter>]
  [TOPK n]
  [LIMIT n]
  [WRAP(<sql>)]
```

### 子句执行顺序

```
WHERE → VERSION → GRAPH → ops(TIME/TOPK/LIMIT 按书写顺序) → LIMIT 截断
```

> **关键**：TIME 是独立子句，**不能用 AND 连接到 WHERE**！
> - ✅ `WHERE subject='星织' TIME year(2026)`  ← 正确
> - ❌ `WHERE subject='星织' AND TIME year(2026)` ← 语法错误

### 字段列表

```sql
SELECT * FROM memories              -- 所有字段
SELECT subject, action FROM memories  -- 指定字段
SELECT ALL FROM memories             -- 等价于 *
```

**合法字段**：
- 槽位字段：`scene`, `subject`, `action`, `object`, `purpose`, `result`
- 元数据字段：`id`, `content`, `lifecycle`, `created_at`, `updated_at`

---

## 三、WHERE 条件

### 方式一：显式字段条件

```sql
WHERE subject='星织' AND action='是'
WHERE lifecycle > 86400
WHERE created_at >= '2026-04-01'
```

**支持的运算符**：

| 运算符 | 示例 | 说明 |
|--------|------|------|
| `=` / `==` | `subject='星织'` | 等于 |
| `!=` | `subject!='星织'` | 不等于 |
| `<` / `>` | `lifecycle > 86400` | 比较 |
| `<=` / `>=` | `lifecycle >= 86400` | 比较 |
| `LIKE` | `subject LIKE '星%'` | 模式匹配（`%`通配） |
| `IN` | `subject IN ['星织','绯绯']` | 列表包含 |

> **注意**：虽然 WHERE 子句内支持 LIKE/IN，但**时间过滤请用 TIME 关键字**，
> 不要用 `WHERE scene LIKE '2026-04%'`，这是错误用法。

### 方式二：跨槽位裸字符串

```sql
WHERE '哥哥' AND '绯绯'   -- 在任意槽位中搜索包含这些关键词的记忆
WHERE '慢慢来'             -- 单个关键词
```

用单引号包裹的关键词会在所有 6 个槽位中做子串匹配，多个关键词用 AND 连接表示全部满足。

### 方式三：向量搜索

```sql
WHERE [subject='星织', action='学', object='编程'] SEARCH TOPK 10
WHERE subject='星织' SEARCH TOPK 5     -- 等值条件自动转为搜索槽位
```

---

## 四、TIME 时间过滤

**TIME 按 `created_at`（记忆创建时间戳）过滤，是独立子句，不属于 WHERE。**

### 语法

```
TIME [year(Y [TO Y | OR Y | *]) [AND month(M [TO M | OR M | *]) [AND day(D [TO D | OR D | *]) [AND clock(HH:MM [TO HH:MM | *])]]]]
```

### 四个维度

| 维度 | 格式 | 取值范围 | 说明 |
|------|------|----------|------|
| `year` | `year(2026)` / `year(2024 TO 2025)` / `year(*)` | 任意正整数 | 年份 |
| `month` | `month(04)` / `month(01 TO 03)` / `month(*)` | 1-12 | 月份 |
| `day` | `day(22)` / `day(01 TO 15)` / `day(*)` | 1-31 | 日期 |
| `clock` | `clock(09:00 TO 18:00)` / `clock(*)` | HH:MM | 日内时段 |

- 单值等价于范围：`year(2026)` = `year(2026 TO 2026)`
- `*` 表示不限制该维度
- 不写的维度 = `*`（不过滤）
- OR 语法：`month(01 OR 09)` 匹配 1 月或 9 月

### 示例

```sql
-- 2024年创建的记忆
SELECT * FROM memories WHERE subject='星织' TIME year(2024)

-- 2024年1-3月
SELECT * FROM memories WHERE subject='星织' TIME year(2024) AND month(01 TO 03)

-- 今天（假设今天是2026-04-26）
SELECT * FROM memories WHERE subject='星织' TIME year(2026) AND month(04) AND day(26)

-- 每天18:00-23:59
SELECT * FROM memories WHERE subject='星织' TIME clock(18:00 TO 23:59)

-- 2025年每月21-30号的下午
SELECT * FROM memories TIME year(2025) AND day(21 TO 30) AND clock(12:30 TO 20:00)
```

### TIME vs scene 槽位

| 对比 | scene 槽位 | TIME 过滤 |
|------|-----------|-----------|
| 含义 | 记忆内容的场景标签（时间场景+空间场景） | 记忆创建时间的时间戳 |
| 语法 | `WHERE scene='平时'` | `TIME year(2024) AND month(01 TO 03)` |
| 场景 | "平时的习惯"、"周末做的事"、"在家的情况" → 搜 scene 槽位 | "2024年的记忆" → TIME 过滤 |

---

## 五、TOPK 排序

过滤后按向量匹配度排序取前 k 条：

```sql
SELECT * FROM memories WHERE subject='星织' TOPK 5
SELECT * FROM memories WHERE subject='星织' TIME year(2026) TOPK 10 LIMIT 5
```

---

## 六、GRAPH 图查询

对已有记忆做多跳关联扩展。

### 操作类型

| 操作 | 语法 | 说明 |
|------|------|------|
| EXPAND | `GRAPH EXPAND(HOPS n)` | 扩展 n 跳邻居 |
| EXPAND | `GRAPH EXPAND(HOPS n MIN_SHARED m)` | 最少共享 m 个槽位 |
| NEIGHBORS | `GRAPH NEIGHBORS(MIN_SHARED m)` | 获取直接邻居 |
| CONNECTED | `GRAPH CONNECTED(MIN_SHARED m)` | 获取连通分量 |
| PATH | `GRAPH PATH(TO 'memory_id')` | 查找到目标的路径 |
| VALUE_CHAIN | `GRAPH VALUE_CHAIN(SLOTS [subject, object])` | 沿槽位值链追踪 |

### 示例

```sql
-- 自我分析
SELECT * FROM memories WHERE subject='星织' GRAPH EXPAND(HOPS 2) LIMIT 20

-- 关系探索
SELECT * FROM memories WHERE '哥哥' GRAPH CONNECTED(MIN_SHARED 2) LIMIT 30
```

---

## 七、VERSION / LIMIT / WRAP

### VERSION

```sql
SELECT * FROM memories WHERE subject='星织' VERSION v1
SELECT * FROM memories WHERE subject='星织' VERSION 2
```

### LIMIT

```sql
SELECT * FROM memories WHERE subject='星织' LIMIT 10
```

### WRAP

```sql
SELECT * FROM memories WRAP(SELECT * FROM memories WHERE subject='星织' LIMIT 5)
```

### DEFINE

```sql
DEFINE my_view AS SELECT * FROM memories WHERE subject='星织' LIMIT 10
```

---

## 八、INSERT 写入语法

### 语法

```sql
INSERT INTO memories VALUES ('<scene><subject><action><object><purpose><result>', 'description', reference_duration);
```

### 三个参数

| 参数 | 格式 | 作用 |
|------|------|------|
| `query_sentence` | 六槽 `<>` 包裹字符串 | 查询时的匹配依据 |
| `content` | 自然语言描述 | 六槽内容的语义解释 |
| `reference_duration` | 整数（秒），可选 | 参考生命周期（默认 86400） |

> `reference_duration` 省略或为 NULL 时，由 LifecycleManager 用默认值 86400 决策。

---

## 九、六槽详解

```
<scene><subject><action><object><purpose><result>
 ①       ②        ③       ④       ⑤        ⑥
```

### ① scene 槽 —— 场景标签

scene 槽是记忆**内容**的场景标签（时间场景+空间场景），不是创建时间戳。按创建时间过滤用 `TIME` 关键字。

**时间场景**：

| 场景词 | 语义 | reference_duration |
|--------|------|---------------------|
| `平时` | 永久事实、习惯性状态 | 999999 |
| `少年期` | 12-15 岁期间 | 999999 |
| `童年` | 幼年时期 | 999999 |
| `那天晚上` | 某次具体事件 | 86400 或 604800 |
| `深夜` | 深夜时段 | 86400 |
| `早上` | 早上时段 | 86400 |
| `晚上` | 泛指晚上 | 86400 |
| `白天` | 泛指白天 | 86400 |
| `周末` | 周末发生的事 | 604800 |
| `假期` | 假期发生的事 | 604800 |
| `本周早些时候` | 本周内事件 | 604800 |
| `YYYY-MM-DD` | 具体日期 | 按重要性 |

**空间场景**：

| 场景词 | 语义 | reference_duration |
|--------|------|---------------------|
| `家里` | 家庭场景 | 86400 |
| `公司` | 工作场景 | 86400 |
| `学校` | 学习场景 | 86400 |
| `户外` | 户外场景 | 86400 |
| `线上` | 网络/线上场景 | 86400 |
| `路上` | 通行场景 | 86400 |

> **禁止**：`<近日>` `<最近>` `<前些时候>` 等模糊词汇。
> 当文本同时暗示时间和空间时，优先选最突出的那个场景维度填入 scene 槽。

### ② subject 槽 —— 主体

执行动作或承受状态的角色：`星织`、`用户`、`哥哥`、`绯绯`、`父亲`、`助手`。

### ③ action 槽 —— 核心动词

| action | 语义 | 填充模式 |
|--------|------|----------|
| `<是>` | 定义身份/类型 | `<scene><subject><是><属性><身份/类别><具体值>` |
| `<有>` | 拥有/存在 | `<scene><subject><有><对象><关系><具体值>` |
| `<与>` | 描述两方关系 | `<scene><subject><与><另一方><关系类型><具体说明>` |
| `<的>` | 归属/属性 | `<scene><subject><的><属性名><属性类别><属性值>` |
| `<叫>` | 命名/称呼 | `<scene><subject><叫><名字><称呼><名字值>` |
| `<差>` | 差异/差距 | `<scene><subject><差><对比对象><差距类别><差值>` |
| `<来自>` | 来源/出身 | `<scene><subject><来自><来源地><身份><无>` |
| `<喜欢>` | 偏好/喜爱 | `<scene><subject><喜欢><对象><喜好><具体对象>` |
| `<知道>` | 知晓/了解 | `<scene><subject><知道><对象><知识类别><无>` |
| `<不知道>` | 未知/不了解 | `<scene><subject><不知道><对象><类别><结论>` |
| `<同意>` | 同意某事 | `<scene><subject><同意><对象><条件><结果>` |
| `<拒绝>` | 拒绝某事 | `<scene><subject><拒绝><对象><原因><无>` |
| `<希望>` | 表达愿望 | `<scene><subject><希望><对象><目标><结果>` |
| `<遵循>` | 遵守规则 | `<scene><subject><遵循><规则名><规则内容><无>` |
| `<发生于>` | 时间点事件 | `<scene><subject><发生于><地点/场景><无><无>` |
| 其他单字动词 | 动作行为 | 按语义自然填充 |

### ④ object 槽 —— 动作承受者/目标

### ⑤ purpose 槽 —— 语义类别

该槽位描述本条记忆回答**什么类型的问题**——即这条记忆的语义维度。填入单一类别词，不写短语。

| 类别词 | 含义 | 示例用途 |
|--------|------|----------|
| `<名字>` | 名称/称呼 | "叫什么名字" |
| `<身份>` | 身份/角色 | "是什么身份" |
| `<关系>` | 人际关系 | "是什么关系" |
| `<年龄差距>` | 年龄差 | "差多少岁" |
| `<喜好>` | 偏好/兴趣 | "喜欢什么" |
| `<经历>` | 过往经历 | "经历过什么" |
| `<性别>` | 性别 | "是男是女" |
| `<密码>` | 密码/凭证 | "密码是什么" |
| `<技能>` | 技能/能力 | "会什么" |
| `<过往>` | 过去/历史 | "以前如何" |
| `<计划>` | 计划/安排 | "打算做什么" |
| `<节奏>` | 进度/节奏 | "进展如何" |
| `<活动>` | 活动/行为 | "在做什么" |

### ⑥ result 槽 —— 具体值/结论

对应 purpose 的答案——具体是什么值、什么结论。填入单一词，不写短语。

### ★ 每个槽位只写一个词，不写短句

槽位是关键词索引，不是叙述文本。多词信息应拆分到不同槽位。

❌ 错误：`object=<被父亲交给哥哥照顾>` — 这是句子，不是词
✅ 正确：拆为 `<是>/<做>/<有>` + `<照顾>/<谁>/<绯绯>`

---

## 十、六槽等长原则

六槽必须严格等长，缺槽用 `<无>` 占位：

```sql
-- ✅ 正确：六个槽位完整，每个槽一个词
INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', 2592000);
INSERT INTO memories VALUES ('<所有><星织><有><哥哥><关系><绯绯>', '星织有个哥哥叫绯绯', 2592000);
INSERT INTO memories VALUES ('<所有><星织><差><绯绯><年龄差距><一岁>', '星织和绯绯只差一岁', 2592000);

-- ❌ 错误：少一个槽
INSERT INTO memories VALUES ('<平时><星织><是><女性><无>', '星织是女性', 999999);

-- ❌ 错误：多一个槽
INSERT INTO memories VALUES ('<平时><星织><是><女性><无><无><无>', '星织是女性', 999999);
```

---

## 十一、UPDATE / DELETE

```sql
-- 修改记忆内容
UPDATE memories SET content='新内容', lifecycle=999999 WHERE id='mem_xxx'

-- 删除记忆
DELETE FROM memories WHERE subject='临时'

-- 预览删除（不实际执行）
DELETE FROM memories WHERE subject='临时' DRYRUN
DELETE FROM memories WHERE subject='临时' DRY RUN
```

---

## 十二、系统管理 & 用户管理

```sql
-- 系统管理
CREATE DATABASE my_system
DROP DATABASE my_system
LIST DATABASES
USE my_system

-- 用户管理
CREATE USER alice
DROP USER alice
LIST USERS
GRANT read ON my_system TO alice
GRANT write ON my_system TO alice
REVOKE read ON my_system FROM alice
GENERATE KEY FOR alice
```

---

## 十三、常见错误汇总

### 1. TIME 不能用 AND 连接到 WHERE

```sql
-- ❌ 错误
SELECT * FROM memories WHERE subject='星织' AND TIME year(2026) AND month(04) AND day(25)

-- ✅ 正确：WHERE 和 TIME 之间没有 AND
SELECT * FROM memories WHERE subject='星织' TIME year(2026) AND month(04) AND day(25)
```

### 2. 时间过滤不要用 WHERE LIKE

```sql
-- ❌ 错误：不要用 LIKE 做时间过滤
SELECT * FROM memories WHERE time LIKE '2026-04-25%'

-- ✅ 正确：用 TIME 关键字
SELECT * FROM memories TIME year(2026) AND month(04) AND day(25)
```

### 3. 六槽数量错误

| 情况 | 示例 |
|------|------|
| ❌ 少一个槽 | `'<平时><星织><是><女性><无>'` |
| ✅ 正确 | `'<平时><星织><是><女性><无><无>'` |

### 4. scene 槽塞入 subject

| 情况 | 示例 |
|------|------|
| ❌ 错误 | `'<平时><早上><哥哥醒来>...'` |
| ✅ 正确 | `'<早上><哥哥><醒来>...'` |

### 5. action 槽包含完整动作描述

| 情况 | 示例 |
|------|------|
| ❌ 错误 | `'<平时><星织><同意与哥哥><发展感情>...'` |
| ✅ 正确 | `'<深夜><星织><同意><哥哥><发展感情>...'` |

### 6. lifecycle 与 scene 不一致

| 情况 | scene | lifecycle |
|------|------|-----------|
| ❌ 错误 | `<本周早些时候>` | 999999 |
| ✅ 正确 | `<本周早些时候>` | 604800 |

---

## 十四、踩坑经验

（以下由 AI 在实际调用中自动积累）

- `<同意>` 槽位填充：object=同意谁/什么，purpose=同意做什么/目的，result=结果/条件
- `<准备>` 是个 action，但它的格式不是标准六槽，需要根据语义灵活填充，避免多加 `<无>`
- lifecycle=86400 的 `<平时>` 全部要改为 `<深夜>`，因为 "平时" 在中文语义里是永久的
