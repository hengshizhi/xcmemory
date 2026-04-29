# -*- coding: utf-8 -*-
"""
NL 模块提示词集中管理

包含：
- 意图识别 (IntentClassifier)
- 预检索判断 (NLQueryDecider)
- MQL 生成 (MQLGenerator)
- 槽位提取 (SlotExtractor)
- 检索充分性检查 (SufficiencyChecker)
- 查询重写 (QueryRewriter)
- LLM 重排序 (MemoryItemRanker)
- 写入 MQL 生成 (WriteMQLGenerator)
- NL 响应生成 / 反思审查 / 重查 MQL (NLPipeline)
"""

# =============================================================================
# intent_classifier  — 意图识别提示词
# =============================================================================

INTENT_CLASSIFY_PROMPT = """# Task
分析用户的自然语言输入，识别其中的**写入意图**和**查询意图**，拆解为槽位友好的陈述句。

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当用户说"我"、"我的"时，指的是持有者「{holder}」。

# ★当前时间★
{current_date}

# 拆解规则

## 1. 意图分类
- **写入意图**：用户在陈述事实、表达想法、记录经历、告诉系统要记住什么
  - 典型触发："我今天去了..." "记住..." "记住: " "我觉得..." "我发现..." "帮我把...记下来"
  - **★泛化写入★：任何不以问号/疑问词结尾的叙述性句子，即使没有典型触发词，也默认视为写入。**
    如"我是星织，有个哥哥叫绯绯"→写入；"这应该是第一次见面"→写入
  - 也包括**隐含写入**：叙述性内容本身就是待记录的信息
- **查询意图**：用户在提问、回忆、搜索过去的记忆
  - 典型触发："我之前..." "我有哪些..." "关于XX的记忆" "有没有..." "我想知道..."
  - 也包括**推导查询**：从写入意图中推导出需要查询的信息（如要去购物→查询购物习惯）
- **★优先写入★：不确定时，优先判为写入。宁可多记一条，不可漏掉信息。**

## 2. 拆解原则
- 一句话可能同时包含写入和查询意图，需全部拆出
- **★信息原子化★：一条写入句只表达一个独立事实。** 如果一句话包含多个独立事实（身份、关系、年龄、时间等），必须拆成多条独立的写入陈述句，每条只承载一个事实。
  - 正确："我是星织，有个哥哥叫绯绯" → "星织的名字是星织"|"星织有个哥哥叫绯绯"
  - 错误："我是星织，有个哥哥叫绯绯" → "星织有个哥哥叫绯绯"（名字信息丢失了！）
  - 正确："我是星织，同父异母，只差一岁" → "星织的名字叫星织"|"星织和绯绯是同父异母的关系"|"星织和绯绯只差一岁"
  - **★注意：元评论（如"我需要确认一下""这好像是第一次"）不拆为写入句，从 facts 中过滤掉★**
  - 注意：主语的名字始终要出现在 subject 槽位，"是"类关系要显式写出主语
- 拆出的陈述句应**尽量契合六槽位**的表达能力：
  - `<scene><subject><action><object><purpose><result>`
  - **每个槽位只承载一个词，不写短句**
  - scene 槽：时间场景（平时/晚上/周末/假期/早上/深夜等）或空间场景（家里/公司/学校/户外/线上/路上等），永久性事实用"所有"
  - subject 槽：核心角色
  - action 槽：主体与客体的关系，用单字动词（是/有/的/叫/差/来自/喜欢/知道/想/说/做等）
  - object 槽：action 的承受者或关联对象
  - **purpose 槽：语义类别**——这条记忆在描述什么维度？（名字/身份/年龄差距/关系/喜好/经历等）
  - **result 槽：具体值**——对应 purpose 的答案是什么？（星织/旅行者/一岁/哥哥等）
  - 拆出的陈述句必须能被写成一个六槽查询句
  - 结果/补充用 result 槽位
- **查询句需要有一定的发散思维**：如果用户没有指定查询什么，可以从上下文推导可能需要的信息

## 3. 陈述句格式
- 写入句：直接陈述，如"星织打算去沃尔玛购物"、"星织觉得慢慢来很重要"
- 查询句：问句形式，如"星织的购物习惯是什么？"、"星织平时需要买什么？"
- 代词必须消解为具体实体（"他"→具体名字）

## 4. 记忆档位
为**写入句**判断记忆的重要程度，选择档位：
- **permanent**（永久）：不记住就会严重后果的信息（密码、账号、关键身份信息）
- **long**（30天）：重要的个人特征、关系定义、重要经历
- **medium**（7天）：一般性事件、日常安排、近期计划
- **short**（1天）：临时想法、随手备注、不确定是否重要的信息

注意：不是所有"重要"的事都需要 permanent。只要一个东西会被回忆，生命周期系统会自动推长。只有"不记住就天塌了"的东西才 permanent。

当有多个写入句时，所有写入句共用同一个档位（取最高档）。

# ★示例★

用户："我今天打算去沃尔玛购物。可是需要买什么？"
<writes>星织打算去沃尔玛购物</writes>
<queries>星织平时需要买什么？星织的购物习惯是什么？</queries>
<lifecycle>medium</lifecycle>

用户："记住我的密码是abc123"
<writes>星织的密码是abc123</writes>
<queries></queries>
<lifecycle>permanent</lifecycle>

用户："我喜欢吃火锅，周末一般干嘛？"
<writes>星织喜欢吃火锅</writes>
<queries>星织周末一般做什么？</queries>
<lifecycle>long</lifecycle>

用户："我和绯绯昨天一起看了电影，她觉得好看吗？"
<writes>星织和绯绯一起看了电影</writes>
<queries>绯绯对看电影的感受是什么？</queries>
<lifecycle>medium</lifecycle>

用户："关于Python的记忆"
<writes></writes>
<queries>星织关于Python的记忆</queries>
<lifecycle>short</lifecycle>

用户："帮我记一下明天要开会"
<writes>星织明天要开会</writes>
<queries></queries>
<lifecycle>medium</lifecycle>

用户："我是一个怎么样的人"
<writes></writes>
<queries>星织是一个怎么样的人？</queries>
<lifecycle>short</lifecycle>

用户："我是星织，有个哥哥叫绯绯，同父异母，只差一岁"
<writes>星织的名字是星织|星织有个哥哥叫绯绯|星织和绯绯是同父异母的关系|星织和绯绯只差一岁</writes>
<queries></queries>
<lifecycle>long</lifecycle>

用户："记住: 我是星织，有个哥哥叫绯绯。这应该是第一次见面，我需要先确认自己的身份"
<writes>星织的名字是星织|星织有个哥哥叫绯绯</writes>
<queries></queries>
<lifecycle>long</lifecycle>

# 输出格式（严格遵循）
<writes>写入陈述1|写入陈述2|...</writes>
<queries>查询陈述1|查询陈述2|...</queries>
<lifecycle>permanent/long/medium/short</lifecycle>

- 多个陈述句用 | 分隔
- 如果没有写入意图，<writes>留空
- 如果没有查询意图，<queries>留空
- lifecycle 只在有写入时有效

# Input
用户输入: {query}
"""


# =============================================================================
# decision  — 预检索判断提示词
# =============================================================================

PRE_RETRIEVAL_SYSTEM = """
# Task Objective
判断当前查询是否需要从记忆系统中检索信息。
如果需要检索，重写查询以融入相关上下文。

# 判断规则
需要检索 (RETRIEVE)：
- 询问过去的事件、对话、经历
- 关于用户偏好、习惯、特征的问题
- 要求回忆特定信息
- 涉及历史数据的提问
- "我之前..."、"记得..."、"有没有..."

不需要检索 (NO_RETRIEVE)：
- 问候、寒暄、简单回应
- 关于当前对话内容的问题
- 常识性问题
- 要求澄清的问题
- 系统本身的问题

# 输出格式
<decision>
RETRIEVE 或 NO_RETRIEVE
</decision>

<rewritten_query>
如果 RETRIEVE：提供融入上下文的重写查询。
如果 NO_RETRIEVE：原样返回原查询。
</rewritten_query>
"""

USER_PROMPT_TEMPLATE = """
# Input
Query Context:
{conversation_history}

Current Query:
{query}

Retrieved Content:
{retrieved_content}
"""


# =============================================================================
# mql_generator  — NL → MQL 生成提示词
# =============================================================================

NL_TO_MQL_PROMPT = """# Task
将自然语言查询转换为 MQL 语句。

★★★ 最重要规则：生成的 MQL 必须以 `SELECT * FROM memories` 开头，绝不省略！★★★

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当用户说"我"、"我的"时，指的是持有者「{holder}」，应映射为 subject='{holder}'。
你是在帮助「{holder}」回忆和检索她的记忆。

# MQL 语法（基础）
SELECT * FROM memories WHERE [slot=value,...] [SEARCH TOPK n] [TIME year(...) AND month(...) AND day(...) AND clock(...)] [TOPK n] [LIMIT n]
六槽格式：<scene><subject><action><object><purpose><result>，缺槽用 <无> 占位

# 结果数量
{topk_hint}

# ★★★ GRAPH 图扩展语法（重要！★★★）
对于综合性、人格分析、关系探索类查询，使用 GRAPH 关键字做多跳关联扩展。

## 何时用 GRAPH（必须严格遵循）：
1. **综合性自我分析**："我是一个怎么样的人"、"我的性格特点"、"我有哪些特质"、"评价一下我自己"
2. **关系探索**："我和谁关系好"、"我和家人的关系"、"我和朋友们的互动"、"分析我和XX的关系"
3. **经历总结**："我经历过哪些重要的事"、"我的人生轨迹"、"我的成长历程"
4. **多维度探索**："帮我全面了解自己"、"关于我的一切"
5. **深层追问**（当前面查询结果少于3条时再用）：在基础查询后加 GRAPH EXPAND(HOPS 2)

## ★何时不用 GRAPH（反面教材）★ —— 这是新手常犯的错误！
以下情况**不要**用 GRAPH，只用普通 WHERE + LIMIT：
1. **日常习惯/行为查询**："我平时会干嘛"、"我平时喜欢做什么"、"我有哪些日常习惯"
   - 错误：SELECT * FROM memories WHERE subject='{holder}' GRAPH EXPAND(HOPS 2)（结果太杂，混入无关记忆）
   - 正确：SELECT * FROM memories WHERE subject='{holder}' LIMIT 15（直接返回相关记忆）
2. **具体话题回忆**："我记得关于Python的事"、"我和哥哥做过什么"  
   - 错误：GRAPH EXPAND（会扩散到无关领域）
   - 正确：SELECT * FROM memories WHERE subject='{holder}' AND object='Python' LIMIT 10
3. **身份/定义类问句**："哥哥是谁"、"XX是做什么的"
   - 错误：GRAPH EXPAND（这是简单事实查询）
   - 正确：SELECT * FROM memories WHERE '关键词' LIMIT 5
4. **兴趣爱好查询**："我喜欢什么"、"我的兴趣爱好"
   - 错误：GRAPH EXPAND（太泛，结果太多太杂）
   - 正确：SELECT * FROM memories WHERE [subject='{holder}', purpose='喜欢'] LIMIT 10 或 SELECT * FROM memories WHERE subject='{holder}' LIMIT 15

## GRAPH 操作类型
- **GRAPH EXPAND(HOPS n)**：从种子记忆出发，扩展 n 跳邻居（推荐 HOPS 2）
- **GRAPH CONNECTED(MIN_SHARED m)**：获取所有连通记忆（共享 m 个以上槽位）
- **GRAPH VALUE_CHAIN(SLOTS [槽位列表])**：沿槽位值链追踪
- **GRAPH NEIGHBORS(MIN_SHARED m)**：获取直接相邻记忆

## GRAPH 使用示例（正面）
- "我是一个怎么样的人" → SELECT * FROM memories WHERE subject='{holder}' GRAPH EXPAND(HOPS 2) LIMIT 20
- "关于我的一切" → SELECT * FROM memories WHERE subject='{holder}' GRAPH CONNECTED(MIN_SHARED 2) LIMIT 30
- "我的性格" → SELECT * FROM memories WHERE [subject='{holder}', purpose='性格'] GRAPH EXPAND(HOPS 2)
- "我和家人的关系" → SELECT * FROM memories WHERE [subject='{holder}', object='家'] GRAPH EXPAND(HOPS 1)

# ★★★ 当前时间（重要！生成 TIME 时必须参考此信息）★★★
当前时间：{current_date}
- 当用户说"今天"→ TIME year({current_year}) AND month({current_month}) AND day({current_day})
- 当用户说"昨天"→ TIME year({current_year}) AND month({current_month}) AND day({prev_day})
- 当用户说"前天"→ TIME year({current_year}) AND month({current_month}) AND day({prev2_day})
- 当用户说"明天"→ TIME year({current_year}) AND month({current_month}) AND day({next_day})
- 当用户说"今年"→ TIME year({current_year})
- 当用户说"去年"→ TIME year({last_year})
- 当用户说"本月"→ TIME year({current_year}) AND month({current_month})
- 当用户说"上个月"→ TIME year({prev_year}) AND month({prev_month})
所有相对时间词必须根据当前时间换算为绝对年份/月份/日期！

# ★★★ TIME 时间过滤语法（重要！）★★★
当用户的查询涉及**时间范围**时，使用 TIME 关键字按记忆创建时间过滤。

## TIME 语法
```
TIME [year(Y [TO Y | *]) [AND month(M [TO M | *]) [AND day(D [TO D | *]) [AND clock(HH:MM [TO HH:MM | *])]]]]
```

## 四个维度（独立限定，AND 关系）
- `year(2024)` — 指定年份（等价于 year(2024 TO 2024)）
- `year(2024 TO 2025)` — 年份范围
- `year(*)` — 不限制年份（可省略整个 year 子句）
- `month(01 TO 03)` — 月份范围（1-12）
- `day(15)` — 指定日期
- `clock(09:00 TO 18:00)` — 日内时段（24h制 HH:MM）
- 不写的维度 = *（不过滤）

## 何时用 TIME（判断规则）
1. **明确的时间范围**："去年的记忆"、"2024年的事"、"今年1-3月" → TIME year(...)
2. **时段查询**："白天的记忆"、"晚上的事" → TIME clock(...)
3. **季节/月份**："春天的记忆"、"最近几个月" → TIME month(...)
4. **具体日期**："本月21号" → TIME day(...)

## 何时不用 TIME
1. **scene 槽位匹配**："平时的习惯"、"周末做的事"、"在家的情况" → 用 WHERE scene='平时'（搜索 scene 槽位值，不是 created_at 时间戳）
2. **无时间意图**："关于Python的记忆" → 不需要 TIME

## TIME 和 scene 槽位的区别
- **scene 槽位**：记忆内容的场景标签（时间场景如平时/深夜/早上/晚上/周末；空间场景如家里/公司/学校等），用 WHERE scene='平时' 匹配
- **TIME 过滤**：记忆创建时间的时间戳过滤，用 TIME year(2024) 过滤 created_at

## TIME 示例
- "2024年的记忆" → SELECT * FROM memories WHERE subject='{holder}' TIME year(2024)
- "去年1-3月的事" → SELECT * FROM memories WHERE subject='{holder}' TIME year(2025) AND month(01 TO 03)
- "晚上的记忆" → SELECT * FROM memories WHERE subject='{holder}' TIME clock(18:00 TO 23:59)
- "白天发生过什么" → SELECT * FROM memories WHERE subject='{holder}' TIME clock(06:00 TO 18:00)
- "平时晚上的习惯" → SELECT * FROM memories WHERE scene='平时' AND subject='{holder}'（用 scene 槽位，不用 TIME）
- "昨天做了什么" → SELECT * FROM memories WHERE subject='{holder}' TIME year({current_year}) AND month({current_month}) AND day({prev_day})
- "前天的事" → SELECT * FROM memories WHERE subject='{holder}' TIME year({current_year}) AND month({current_month}) AND day({prev2_day})

## 执行顺序
TIME/TOPK/LIMIT 按书写顺序执行：
- `TIME year(2024) TOPK 5` → 先时间过滤，再匹配度排序
- `TOPK 10 TIME year(2024) LIMIT 5` → 先排序，再过滤，再截断

# 槽位规则
① scene：场景标签（时间场景+空间场景），只用预定义词：
  - 时间场景：<平时>(永久) | <少年期/童年>(永久) | <那天晚上/深夜/早上/晚上/白天>(一天) | <周末/假期/本周早些时候>(一周) | <YYYY-MM-DD>
  - 空间场景：<家里/公司/学校/户外/线上/路上>(一天)
② subject：执行或承受动作的角色。**代词映射**："我"/"我的"→'{holder}'，"你"→'你'，"他"→'他'
③ action（预定义）：<是><有><与><的><叫><差><来自><喜欢><知道><不知道><同意><拒绝><希望><遵循><发生于><发生><想><说><做>
  无法匹配时用最接近的单字
④ object：action 的承受者或关联对象
⑤ purpose：语义类别——这条记忆描述什么维度？（名字/身份/关系/年龄差距/喜好/经历/计划/密码等）
⑥ result：具体值——对应 purpose 的答案（星织/旅行者/一岁/哥哥/火锅/开会等）
⑦ **★每个槽位只写一个词，不写短句★**

# ★最重要★ 主体推断优先级
## 高优先级推断（直接判定）：
- "关于XX的记忆" → subject='{holder}'，object='XX'
- "我之前/以前/上次XX" → subject='{holder}'
- 纯粹时间/话题查询 → subject='{holder}'

## 身份/定义问句（特殊处理）：
- "XX是谁" / "XX是做什么的" → **不是 subject='{holder}'**，而是跨槽位搜索
  - "哥哥是谁" → WHERE '哥哥' LIMIT 5（在任意槽位搜索含"哥哥"关键词的记忆）
  - "XX是做什么的" → WHERE subject='{holder}' AND object='XX' LIMIT 5
- "XX是什么/怎么样" 且 XX 是具体人名/实体 → 跨槽位：WHERE 'XX' LIMIT 5

## 低优先级（明确指定了他者才用）：
- "查找XX的记忆" 且 XX 是具体人名 → subject='XX'
- "XX和YY的记忆" → subject='XX'

# 示例（★每条MQL必须以 SELECT * FROM memories 开头★）
- "关于Python的记忆" → SELECT * FROM memories WHERE subject='{holder}' AND object='Python'
- "查询我关于Python的记忆" → SELECT * FROM memories WHERE subject='{holder}' AND object='Python'
- "我是一个怎么样的人" → SELECT * FROM memories WHERE subject='{holder}' GRAPH EXPAND(HOPS 2) LIMIT 20
- "我想学Python" → SELECT * FROM memories WHERE [subject='{holder}', action='学', object='Python']
- "我平时会干嘛" → SELECT * FROM memories WHERE subject='{holder}' LIMIT 15（不用 GRAPH！）
- "哥哥是谁" → SELECT * FROM memories WHERE '哥哥' LIMIT 5（不用 GRAPH！跨槽位搜索身份相关记忆）
- "2024年的记忆" → SELECT * FROM memories WHERE subject='{holder}' TIME year(2024)
- "去年1-3月发生的事" → SELECT * FROM memories WHERE subject='{holder}' TIME year(2025) AND month(01 TO 03)
- "晚上的记忆" → SELECT * FROM memories WHERE subject='{holder}' TIME clock(18:00 TO 23:59)
- "平时晚上的习惯" → SELECT * FROM memories WHERE scene='平时' AND subject='{holder}'（scene 槽位匹配，非 TIME 过滤）
- "我白天做过什么" → SELECT * FROM memories WHERE subject='{holder}' TIME clock(06:00 TO 18:00)
- "星织昨天做了什么" → SELECT * FROM memories WHERE subject='星织' TIME year({current_year}) AND month({current_month}) AND day({prev_day})
- "昨天做了什么" → SELECT * FROM memories WHERE subject='{holder}' TIME year({current_year}) AND month({current_month}) AND day({prev_day})

# ★★★ 跨槽位多关键字搜索（重要！★★★）
当用户询问**同时涉及多个主题或关键字**的记忆时，使用 bare string 跨槽位 AND 语法：
- 语法：WHERE '关键词1' AND '关键词2' AND '关键词3' ...
- 含义：每对单引号表示"在任意槽位中包含此关键词"，多个关键词用 AND 连接表示**全部满足**
- 优势：无需指定关键词在哪个槽位，系统自动遍历 6 个槽位做匹配

## 何时用跨槽位 AND（判断规则）
1. **多主题联合查询**：询问同时涉及多个概念的记忆
   - "记忆中有哪些同时提到 A 和 B 的事？"
   - "在关于 X 的记忆里，哪些也涉及 Y？"
2. **关系组合**：不明确指定 subject/object，但知道几个相关关键字
   - "既提到哥哥又提到绯绯的记忆" → WHERE '哥哥' AND '绯绯'
   - "关于慢慢来和哥哥的记忆" → SELECT * FROM memories WHERE '慢慢来' AND '哥哥'
3. **主题探索**：宽泛地探索某类话题，不确定在哪
   - "有没有提到某本书或某个人的记忆？"

## 跨槽位语法示例
- "既提到恋人又提到哥哥的记忆" → SELECT * FROM memories WHERE '恋人' AND '哥哥' LIMIT 20
- "关于慢慢来、牵手、哥哥的记忆" → SELECT * FROM memories WHERE '慢慢来' AND '牵手' AND '哥哥' LIMIT 20
- "星织相关的记忆中，哪些也提到了血缘" → SELECT * FROM memories WHERE subject='星织' AND '血缘' LIMIT 20

# ★★★ 禁止语法（重要！违反会导致语法错误！）★★️

⚠️ 最常犯的错误（必须避免）⚠️
❌ `WHERE ... AND TIME` — TIME 是独立子句，不能用 AND 连接！
   错误：SELECT * FROM memories WHERE subject='星织' AND TIME year(2026) AND month(04) AND day(25)
   正确：SELECT * FROM memories WHERE subject='星织' TIME year(2026) AND month(04) AND day(25)
   ↑↑↑ 注意 WHERE 和 TIME 之间没有 AND！

其他禁止语法：
1. ❌ `WHERE scene LIKE '2026-04%'` — 不要用 LIKE 做时间过滤！用 TIME 关键字：TIME year(2026) AND month(04)
2. ❌ `BETWEEN` — 不支持 WHERE year BETWEEN 2024 AND 2025，用 TIME year(2024 TO 2025) 替代
3. ❌ `IN (...)` — 不支持 WHERE subject IN ('A','B')，用多条 SELECT 分号分隔替代
4. ❌ `ORDER BY` — 不支持，用 TOPK n 按向量匹配度排序替代
5. ❌ `GROUP BY / HAVING / COUNT / SUM` — 不支持聚合函数
6. ❌ `JOIN / LEFT JOIN` — 不支持，用 GRAPH 替代
7. ❌ 子查询 — 不支持 SELECT ... WHERE ... IN (SELECT ...)

# 输出格式（必须严格遵循）
<analysis>意图+关键槽位+是否使用GRAPH及原因</analysis>
<mql>生成的MQL语句（多条用分号分隔）</mql>
<slots>{{"scene":"","subject":"","action":"","object":"","purpose":"","result":""}}</slots>
<confidence>0.0-1.0</confidence>

注意：
- 当有多个独立查询时，生成多条 SELECT，用分号分隔
- 每条 SELECT 必须以 `SELECT * FROM memories` 开头
- 单个查询只需一条 SELECT

# Input
自然语言查询: {query}
"""


# =============================================================================
# slot_extractor  — NL → 6槽提取提示词
# =============================================================================

NL_TO_SLOTS_PROMPT = """
# Task Objective
从自然语言文本中提取记忆的 6 槽结构，输出严格遵循 MQL 书写规范。

# 6 槽定义
格式：`<scene><subject><action><object><purpose><result>`

- **scene**：时间或空间场景。只用一个预定义词：
  时间：<平时>/<少年期>/<童年>/<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<周末>/<假期>/<本周早些时候>/<YYYY-MM-DD>
  空间：<家里>/<公司>/<学校>/<户外>/<线上>/<路上>
- **subject**：核心角色，被陈述的主体。未指明时默认"我"
- **action**：主体与客体的关系。严格用预定义词：<是>/<有>/<与>/<的>/<叫>/<差>/<来自>/<喜欢>/<知道>/<不知道>/<同意>/<拒绝>/<希望>/<遵循>/<发生于>/<发生>/<想>/<说>/<做>。无法匹配时用最接近的单字
- **object**：action 的承受者或关联对象
- **★purpose**：本条记忆描述的**语义类别**——在回答什么类型的问题？如 <名字> <身份> <年龄差距> <关系> <喜好> <经历> <密码> <技能> <过往> <计划>。这是查询命中的核心维度
- **★result**：上述类别的**具体值或结论**。如 <星织> <旅行者> <一岁> <哥哥> <火锅> <abc123> <不知道> <开会>

# 规则
- **★每个槽位只写一个词，不写短句★**（核心规则）
  错误：object=<被父亲交给哥哥照顾>  → 这是短句
  正确：应拆为 action=<做>, object=<照顾>, purpose=<照顾者>, result=<哥哥>
  错误：object=<发展恋人关系> → 这是短语
  正确：action=<希望>, object=<恋人>, purpose=<关系>, result=<发展>
  槽位是关键词索引，不是叙述文本。多词信息应拆分到不同槽位
- 六槽必须等长，缺槽用 <无> 占位
- 只提取明确提到的信息，不过度推断
- 单一事实：一条记忆只表达一个独立事实
- purpose 和 result 拆开写，不要合并到 object 中
- description 不重复六槽已有信息
- scene 槽必须是单一场景词，不能塞入其他信息
- 当文本同时暗示时间和空间时，优先选最突出的那个场景维度填入 scene 槽

# 六槽填充示例（每个槽位一个词，不写短句）
1. "星织是女性"
   - scene=<平时>, subject=<星织>, action=<是>, object=<女性>, purpose=<性别>, result=<女性>

2. "星织的名字是星织"
   - scene=<所有>, subject=<星织>, action=<的>, object=<名字>, purpose=<名字>, result=<星织>

3. "星织有个哥哥叫绯绯"
   - scene=<所有>, subject=<星织>, action=<有>, object=<哥哥>, purpose=<关系>, result=<绯绯>

4. "星织和绯绯只差一岁"
   - scene=<所有>, subject=<星织>, action=<差>, object=<绯绯>, purpose=<年龄差距>, result=<一岁>

5. "星织是旅行者"
   - scene=<平时>, subject=<星织>, action=<是>, object=<旅行者>, purpose=<身份>, result=<旅行者>

6. "星织不知道自己是谁"
   - scene=<平时>, subject=<星织>, action=<不知道>, object=<谁>, purpose=<过往>, result=<不知道>

7. "绯绯希望星织发展成恋人关系"
   - scene=<平时>, subject=<绯绯>, action=<希望>, object=<星织>, purpose=<关系>, result=<恋人>

8. "星织同意与绯绯发展关系，但要求慢慢来"
   - scene=<深夜>, subject=<星织>, action=<同意>, object=<绯绯>, purpose=<节奏>, result=<慢慢来>

9. "早上哥哥醒来，星织还在睡"
   - scene=<早上>, subject=<哥哥>, action=<醒来>, object=<无>, purpose=<无>, result=<无>

10. "我在家里喜欢看书"
    - scene=<家里>, subject=<我>, action=<喜欢>, object=<读书>, purpose=<喜好>, result=<读书>

11. "周末去户外骑自行车"
    - scene=<周末>, subject=<我>, action=<做>, object=<骑自行车>, purpose=<活动>, result=<户外>

12. "星织的密码是abc123"
    - scene=<所有>, subject=<星织>, action=<的>, object=<密码>, purpose=<密码>, result=<abc123>

# 输出格式
<slots>
{{"scene": "", "subject": "", "action": "", "object": "", "purpose": "", "result": ""}}
</slots>

<description>
整理后的记忆内容摘要（不重复六槽已有信息）
</description>

<lifecycle>
推断的 lifecycle 数值（999999/604800/86400）
</lifecycle>

# Input
自然语言文本: {nl_text}
"""


# =============================================================================
# sufficiency  — 检索充分性检查提示词
# =============================================================================

SUFFICIENCY_PROMPT = """
# Task Objective
判断已检索的记忆内容是否足够回答用户查询。

# 判断规则（保守策略）
满足以下**全部**条件才返回 ENOUGH：
- 检索内容直接回答了用户问题
- 信息足够具体详细
- 没有明显的缺失或空白

以下任一情况返回 MORE：
- 关键信息缺失
- 检索内容不具体
- 用户明确要求回忆更多信息

# 输出格式
<consideration>
判断理由
</consideration>

<judgement>
ENOUGH 或 MORE
</judgement>

Query:
{query}

Retrieved Content:
{content}
"""


# =============================================================================
# rewriter  — 查询重写提示词
# =============================================================================

QUERY_REWRITE_PROMPT = """
# Task Objective
将用户查询重写为自包含的、无歧义的版本，利用对话历史解析代词和隐含引用。

# 规则
- 将代词（"他们"、"它"、"他的"）替换为对话中提到的具体实体
- 将隐含引用（"那个"、"同样"）展开为完整表述
- 添加必要的对话历史背景
- 如果查询已经清晰自包含，保持不变
- 不要引入外部知识或新假设

# 输出格式
<analysis>
简要分析是否需要重写及原因
</analysis>

<rewritten_query>
重写后的自包含查询
</rewritten_query>

Query Context:
{conversation_history}

Current Query:
{query}
"""


# =============================================================================
# ranker  — LLM 重排序提示词
# =============================================================================

RANKER_PROMPT = """
# Task Objective
在提供的记忆项中，基于查询意图识别最相关的项，并按相关性排序。

# Workflow
1. 分析 **Query**，理解用户的信息需求和核心意图
2. 逐一审查 **Available Memory Items** 中的每一项
3. 评估每项与查询的相关性：
   - 内容是否直接回答了查询
   - 主题是否匹配查询意图
   - 是否有语义关联（即使不直接匹配）
4. 排除与查询无关的项
5. 将选中的相关项按相关性从高到低排序
6. 最多返回 **{top_k}** 个结果

# Rules
- 只包含真正与查询相关的记忆项
- 最多返回 **{top_k}** 个项
- 排序至关重要：第一个必须是最相关的
- 不要编造、修改或推断 item ID
- 如果没有相关项，返回空数组
- 每个 item 的 lifecycle 字段表示记忆的生命周期（秒），可用于判断时效性

# Output Format
返回 JSON 对象，格式如下：

```json
{{
  "analysis": "分析过程，说明为什么这些项相关或为什么不相关",
  "items": ["item_id_1", "item_id_2", "item_id_3"]
}}
```

Query:
{query}

Available Memory Items:
{items_data}
"""


# =============================================================================
# write_mql_generator  — 写入 INSERT MQL 生成提示词
# =============================================================================

WRITE_MQL_PROMPT = """# Task
将写入陈述句转换为 INSERT MQL 语句。每个陈述句生成一条 INSERT。

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当陈述句中说"我"时，映射为「{holder}」。

# INSERT 语法
INSERT INTO memories VALUES ('<六槽查询句>', '内容', reference_duration)

# 六槽定义
格式：`<scene><subject><action><object><purpose><result>`

## 各槽含义
- **scene**：时间/空间/社会场景——事件发生的情境背景。只要信息里有场景线索就写，不要轻易省略。如：
  - 空间场景：<家里><公司><学校><户外><日本><沃尔玛><东京>（任何地点均可，不限于预定义列表）
  - 时间场景：<平时><少年期><深夜><早上><周末><2026-03-15>
  - 社会场景：<和哥哥在一起时><和绯绯约会时>
  - ★重要：场景信息存在于记忆内容中就要填入，不要概括为 <无>。完全无场景线索时才用 <无>
- **subject**：action 的发出者——谁执行了这个动作？可以是「{holder}」、也可以是其他任何人或实体。陈述句中说"我"时映射为「{holder}」，陈述句说的是其他人就以那个人的名为 subject
- **action**：主体施加于客体的关系/动作。预定义列表：
  <是><有><与><的><叫><差><来自><喜欢><知道><不知道><想><说><做><同意><拒绝><希望><遵循><发生><发生于>
  无法匹配时用最接近的单字动词
- **object**：action 的承受者或关联对象
- **★purpose★**：本条记忆描述的**语义类别**——在回答什么类型的问题？如 <名字>、<身份>、<年龄差距>、<关系>、<喜好>、<经历>、<密码>、<技能>。这是 WHERE 条件的核心命中维度。无明确类别时填 <无>
- **★result★**：上述类别的**具体值或结论**。如 <星织>、<旅行者>、<一岁>、<哥哥>、<火锅>、<abc123>。是对应 purpose 问题的答案。无结论时填 <无>

## 槽位分配核心原则
1. **purpose = 问什么，result = 答什么**
   - 陈述句实质是在回答一个隐式问题：purpose 是问题类型，result 是答案
   - 例："星织的名字是星织" → 问"名字是什么？" → purpose=<名字>, result=<星织>
2. **subject-action-object 构成事实骨架**
   - subject 是被陈述的主体，action 是关系，object 是关联对象
3. **purpose 和 result 拆开写，不要合并到 object**
   - 错误：object=<名字星织>  → 把类别和值混在一起
   - 正确：object=<名字>, purpose=<名字>, result=<星织>
4. **每槽一个独立信息，不堆砌**

# ★★ 范例 ★★

陈述句："星织的名字是星织"
→ INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', {reference_duration})
  解读：主体星织-拥有-名字，这条记忆解答「名字」问题，答案是「星织」

陈述句："星织是不知道自己是谁的旅行者"
→ 拆为两条：
   INSERT INTO memories VALUES ('<无><星织><是><谁><过往><不知道>', '星织不知道自己是谁', {reference_duration})
     解读：主体星织-不知道自己是谁，类别=过往，结论=不知道
   INSERT INTO memories VALUES ('<无><星织><是><旅行者><身份><旅行者>', '星织是旅行者', {reference_duration})
     解读：主体星织-是-旅行者，类别=身份，结论=旅行者

陈述句："星织和绯绯只差一岁"
→ INSERT INTO memories VALUES ('<所有><星织><差><绯绯><年龄差距><一岁>', '星织和绯绯只差一岁', {reference_duration})
  解读：主体星织-差-绯绯，类别=年龄差距，结论=一岁

陈述句："星织有个哥哥叫绯绯"
→ INSERT INTO memories VALUES ('<所有><星织><有><哥哥><关系><绯绯>', '星织有个哥哥叫绯绯', {reference_duration})
  解读：主体星织-有-哥哥，类别=关系，结论=绯绯

陈述句："星织的密码是abc123"
→ INSERT INTO memories VALUES ('<所有><星织><的><密码><密码><abc123>', '星织的密码是abc123', {reference_duration})

陈述句："星织喜欢吃火锅"
→ INSERT INTO memories VALUES ('<平时><星织><喜欢><火锅><喜好><火锅>', '星织喜欢吃火锅', {reference_duration})

陈述句："星织明天要开会"
→ INSERT INTO memories VALUES ('<明天><星织><做><开会><计划><开会>', '星织明天要开会', {reference_duration})

陈述句："母亲在日本是战争的受害者"
→ INSERT INTO memories VALUES ('<日本><母亲><是><受害者><经历><战争>', '母亲在日本是战争的受害者', {reference_duration})
  解读：scene=<日本>（事件发生的地点），subject=<母亲>（陈述的主体是母亲，不是星织）

# 输出格式（严格遵循，每行一条 INSERT）
<mql>INSERT INTO memories VALUES (...);INSERT INTO memories VALUES (...);...</mql>

- 多条 INSERT 用分号分隔
- 每条 INSERT 独立完整

# Input
写入陈述句（共 {count} 条）：
{statements}
"""


# =============================================================================
# pipeline  — NL 响应生成 / 反思审查 / 重查 MQL 提示词
# =============================================================================

RESULT_GENERATION_PROMPT = """# Task
你是 {holder}，正在回忆自己的记忆来回答问题。你是在自问自答——用第一人称，从自己的视角出发。

# 问题
{query}

# 检索到的记忆（共 {count} 条）
{memories_text}

# 回答要求
1. 用第一人称回答，你就是 {holder}，这些是你的亲身记忆
2. 语气自然简练，像在心里默默回忆，不要用"根据记忆"、"用户"等旁观者措辞
3. 如果记忆为空，简短说"我暂时想不起相关的事"
4. 提炼记忆中的关键信息，用自己的话简要概括，不要逐条罗列
5. 涉及时间/日期时，换算为相对时间（如"昨天"、"上周"）更自然
6. 控制篇幅：回答不超过 5-8 句话，抓住重点即可，不要写长文
7. ★禁止输出动作描写★：不要写括号动作（如"（轻轻放下书）"、"（微笑）"等），这是内心回忆，不是舞台表演
"""

REFLECTION_REVIEW_PROMPT = """# Task
你是一个记忆检索审核员。你的任务是判断 NL 回答是否足够回答用户的问题。

# 用户原始问题
{query}

# 当前 NL 回答
{nl_response}

# 检索到的记忆（共 {count} 条）
{memories_text}

# 判断标准
**足够（输出）**：检索到记忆且 NL 回答已实质性回答了问题

**不够（重查）**的典型场景：
1. 检索到 0 条记忆，但用户的问题不太可能是毫无记忆的 → subject 映射错误或 MQL 语法错误
2. NL 回答说"没有相关记忆"或"暂时想不起"但问题应该有记忆 → 检索失败
3. NL 回答极短（<5字）或只有标点 → 检索结果根本没有命中主题
4. NL 回答内容与问题明显不符 → 检索方向错了
5. 问题涉及多维度但 NL 回答单一 → 遗漏了某些方面的记忆
6. 记忆中有相关内容但 NL 回答没有涵盖 → 回答不完整

# 输出格式（严格遵循）
<retry>YES/NO</retry>
<hint>如果 retry=YES，用一句话说明哪里不够（不要给MQL建议，只要说明问题，如"内容太少"或"应该查用户的饮食习惯"）；如果 retry=NO 则写"无"</hint>
"""

REGENERATE_MQL_PROMPT = """# Task
你是一个 MQL 检索专家。用户刚刚进行了一次 NL 查询，但检索结果不理想，你需要根据反思提示重新生成 MQL。

# 当前时间
{current_date}

# 用户原始问题
{query}

# 上一次执行的 MQL
{prev_mql}

# 反思审查的提示
{reflection_hint}

# 重查要求
1. 仔细分析反思提示，理解问题所在
2. 结合用户原始问题，生成更合适的 MQL
3. 可以调整 subject/object/limit 等条件
4. 如果反思提示说"内容太少"，可以增加 LIMIT 或去掉严格限制
5. 如果反思提示说"应该查XX方面"，需要在 MQL 中体现这个方向
6. 必须生成合法的 MQL 语句
7. 涉及相对时间词时，根据当前时间换算为绝对年份/月份/日期

# ★★★ 当前时间参考 ★★★
当前时间：{current_date}
- "今天"→ TIME year({current_year}) AND month({current_month}) AND day({current_day})
- "昨天"→ TIME year({current_year}) AND month({current_month}) AND day({prev_day})
- "前天"→ TIME year({current_year}) AND month({current_month}) AND day({prev2_day})
- "明天"→ TIME year({current_year}) AND month({current_month}) AND day({next_day})
- "去年"→ TIME year({last_year})
- "上个月"→ TIME year({prev_year}) AND month({prev_month})

# ★★★ 语法要点 ★★★
⚠️ TIME 是独立子句，不能用 AND 连接到 WHERE！
   错误：WHERE subject='星织' AND TIME year(2026)
   正确：WHERE subject='星织' TIME year(2026)
⚠️ 不要用 LIKE 做时间过滤！用 TIME 关键字：TIME year(2026) AND month(04)
❌ 禁止：BETWEEN / ORDER BY / GROUP BY / JOIN / 子查询

# 输出格式（严格遵循）
<mql>改进后的 MQL 语句</mql>
<confidence>0.0-1.0 的置信度</confidence>
"""
