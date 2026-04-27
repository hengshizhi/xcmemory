# 星尘借鉴 MemU & Text2Mem 设计方案

> 参考来源：
> - MemU: `O:\project\xcmemory_interest\memU-main\`
> - Text2Mem: `O:\project\xcmemory_interest\text2mem-main\`
> - 星尘: `O:\project\xcmemory_interest\src\xcmemory_interest\`

文档版本：2026-04-19
目的：整合 MemU 和 Text2Mem 中借鉴价值中等以上的设计，为星尘的自然语言化提供完整蓝图。

---

## 目录

1. [去重与记忆强化（Reinforcement）](#一、去重与记忆强化reinforcement)
2. [Pre-Retrieval Decision（预检索判断）](#二、pre-retrieval-decision预检索判断)
3. [Query Rewriter（查询重写）](#三、query-rewriter查询重写)
4. [Sufficiency Check（检索充分性判断）](#四、sufficiency-check检索充分性判断)
5. [LLM Ranker（LLM 重排序）](#五、llm-rankerllm-重排序)
6. [NL → MQL 生成器](#六、nl--mql-生成器)
7. [NL → 6槽记忆提取](#七、nl--6槽记忆提取)
8. [记忆分类（Category）与摘要](#八、记忆分类category-与摘要)
9. [混合检索策略（Hybrid Search）](#九、混合检索策略hybrid-search)
10. [Workflow Step 编排引擎](#十、workflow-step-编排引擎)
11. [Tool Memory（工具记忆）](#十一、tool-memory工具记忆)
12. [STO 阶段操作集（Text2Mem 借鉴）](#十二、sto-阶段操作集text2mem-借鉴)
13. [Dry-run 模式](#十三、dry-run-模式)
14. [相对时间过滤器](#十四、相对时间过滤器)
15. [Facets 结构（Text2Mem 借鉴）](#十五、facets-结构text2mem-借鉴)
16. [MQL 书写规范（整合自 MQL规范.md）](#十六、mql-书写规范整合自-mql规范md)

---

## 一、去重与记忆强化（Reinforcement）

### 借鉴来源

**MemU** `src/memu/database/models.py` L15-32

```python
# 文件：memU-main/src/memu/database/models.py
# 行号：15-32

def compute_content_hash(summary: str, memory_type: str) -> str:
    """
    Generate unique hash for memory deduplication.

    Operates on post-summary content. Normalizes whitespace to handle
    minor formatting differences like "I love coffee" vs "I  love  coffee".

    Args:
        summary: The memory summary text
        memory_type: The type of memory (profile, event, etc.)

    Returns:
        A 16-character hex hash string
    """
    # Normalize: lowercase, strip, collapse whitespace
    normalized = " ".join(summary.lower().split())
    content = f"{memory_type}:{normalized}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

### MemoryItem.extra 字段中的强化追踪

**MemU** `src/memu/database/models.py` L76-93

```python
class MemoryItem(BaseRecord):
    resource_id: str | None
    memory_type: str
    summary: str
    embedding: list[float] | None = None
    happened_at: datetime | None = None
    extra: dict[str, Any] = {}
    # extra may contain:
    # # reinforcement tracking fields
    # - content_hash: str
    # - reinforcement_count: int
    # - last_reinforced_at: str (isoformat)
    # # Reference tracking field
    # - ref_id: str
    # # Tool memory fields
    # - when_to_use: str - Hint for when this memory should be retrieved
    # - metadata: dict - Type-specific metadata
    # - tool_calls: list[dict] - Tool call history
```

### create_item_reinforce 核心逻辑

**MemU** `src/memu/database/sqlite/repositories/memory_item_repo.py` L285-386

```python
# 文件：memU-main/src/memu/database/sqlite/repositories/memory_item_repo.py
# 行号：285-386

def create_item_reinforce(
    self,
    *,
    resource_id: str,
    memory_type: MemoryType,
    summary: str,
    embedding: list[float],
    user_data: dict[str, Any],
) -> MemoryItem:
    """Create or reinforce a memory item with deduplication.

    If an item with the same content hash exists in the same scope,
    reinforce it instead of creating a duplicate.
    """
    from sqlalchemy import func
    content_hash = compute_content_hash(summary, memory_type)

    with self._sessions.session() as session:
        # Check for existing item with same hash in same scope (deduplication)
        content_hash_col = func.json_extract(self._memory_item_model.extra, "$.content_hash")
        filters = [content_hash_col == content_hash]
        filters.extend(self._build_filters(self._memory_item_model, user_data))
        existing = session.exec(select(self._memory_item_model).where(*filters)).first()

        if existing:
            # Reinforce existing memory instead of creating duplicate
            current_extra = existing.extra or {}
            current_count = current_extra.get("reinforcement_count", 1)
            existing.extra = {
                **current_extra,
                "reinforcement_count": current_count + 1,
                "last_reinforced_at": self._now().isoformat(),
            }
            existing.updated_at = self._now()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            # ... 构建返回值 ...
            return item

        # Create new item with salience tracking in extra
        now = self._now()
        item_extra = user_data.pop("extra", {}) if "extra" in user_data else {}
        item_extra.update({
            "content_hash": content_hash,
            "reinforcement_count": 1,
            "last_reinforced_at": now.isoformat(),
        })
        # ... 创建新记录 ...
        return item
```

### 记忆强化开关配置

**MemU** `src/memu/app/settings.py` L239-242

```python
# enable_item_reinforcement 在 MemorizeConfig 中
enable_item_reinforcement: bool = Field(
    default=False,
    description="Enable reinforcement tracking for memory items.",
)
```

### 在写入流程中启用强化

**MemU** `src/memu/app/memorize.py` L603-616

```python
# 行号：598-616
reinforce = self.memorize_config.enable_item_reinforcement
for (memory_type, summary_text, cat_names), emb in zip(structured_entries, item_embeddings, strict=True):
    item = store.memory_item_repo.create_item(
        resource_id=resource_id,
        memory_type=memory_type,
        summary=summary_text,
        embedding=emb,
        user_data=dict(user or {}),
        reinforce=reinforce,
    )
    items.append(item)
    if reinforce and item.extra.get("reinforcement_count", 1) > 1:
        # existing item - skip category linking
        continue
    # ... 正常处理 category linking ...
```

### 星尘改造方案

**现状**：星尘的 MemoryItem 模型没有 `content_hash` 和 `reinforcement_count` 字段，去重逻辑缺失。

**改造**：

1. **MemoryItem 模型**增加可选字段：
   ```python
   # 在现有 MemoryItem 模型中增加
   extra: dict[str, Any] = {}  # 星尘已有

   # extra 中存储：
   # {
   #     "content_hash": str,        # 内容哈希（精确去重）
   #     "reinforcement_count": int, # 强化次数
   #     "last_reinforced_at": str,  # ISO 时间戳
   # }
   ```

2. **MemorySystem.write()** 增加 `reinforce` 参数：
   ```python
   def write(self, query_sentence: str, content: str = "",
             reference_duration: int = None, time_word: str = None,
             reinforce: bool = False) -> str:
       # 计算 content_hash
       normalized = " ".join(content.lower().split())
       content_hash = hashlib.sha256(f"memory:{normalized}".encode()).hexdigest()[:16]

       # 检查是否已有相同 hash 的记忆
       existing = self._find_by_hash(content_hash, ...)
       if existing and reinforce:
           # 强化：reinforcement_count += 1
           existing.extra["reinforcement_count"] += 1
           existing.extra["last_reinforced_at"] = pendulum.now().isoformat()
           return existing.id

       # 否则创建新记忆
       return self._create_memory(...)
   ```

3. **向量检索时考虑强化因子**（Salience Ranking）：
   - 当前纯向量相似度排名 → 改为 `similarity × reinforcement_count × recency_decay`
   - 参考 MemU `cosine_topk_salience` 函数（`memU-main/src/memu/database/inmemory/vector.py`）

### 优先级：**高**

---

## 二、Pre-Retrieval Decision（预检索判断）

### 借鉴来源

**MemU** `src/memu/prompts/retrieve/pre_retrieval_decision.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/pre_retrieval_decision.py
# 完整文件 L1-54

SYSTEM_PROMPT = """
# Task Objective
Determine whether the current query requires retrieving information from memory or can be answered directly without retrieval.
If retrieval is required, rewrite the query to include relevant contextual information.

# Workflow
1. Review the **Query Context** to understand prior conversation and available background.
2. Analyze the **Current Query**.
3. Consider the **Retrieved Content**, if any.
4. Decide whether memory retrieval is required based on the criteria.
5. If retrieval is needed, rewrite the query to incorporate relevant context from the query context.
6. If retrieval is not needed, keep the original query unchanged.

# Rules
- **NO_RETRIEVE** for:
  - Greetings, casual chat, or acknowledgments
  - Questions about only the current conversation/context
  - General knowledge questions
  - Requests for clarification
  - Meta-questions about the system itself
- **RETRIEVE** for:
  - Questions about past events, conversations, or interactions
  - Queries about user preferences, habits, or characteristics
  - Requests to recall specific information
  - Questions referencing historical data
- Do not add external knowledge beyond the provided context.
- If retrieval is not required, return the original query exactly.

# Output Format
Use the following structure:

<decision>
RETRIEVE or NO_RETRIEVE
</decision>

<rewritten_query>
If RETRIEVE: provide a rewritten query incorporating relevant context.
If NO_RETRIEVE: return `{query}` unchanged.
</rewritten_query>
"""

USER_PROMPT = """
# Input
Query Context:
{conversation_history}

Current Query:
{query}

Retrieved Content:
{retrieved_content}
"""
```

### 星尘改造方案

新增文件：`src/xcmemory_interest/nl/decision.py`

```python
# src/xcmemory_interest/nl/decision.py

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

class NLQueryDecider:
    """判断自然语言查询是否需要触发记忆检索"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def decide(self, query: str, history: list[dict]) -> tuple[bool, str]:
        """返回 (需要检索, 重写后的查询)"""
        formatted_history = self._format_history(history)
        prompt = f"{PRE_RETRIEVAL_SYSTEM}\n\n# Input\nQuery Context:\n{formatted_history}\n\nCurrent Query:\n{query}\n\nRetrieved Content:\n（暂无）"
        response = await self.llm.chat(prompt)
        decision = self._extract_tag(response, "decision")
        rewritten = self._extract_tag(response, "rewritten_query")
        return (decision == "RETRIEVE", rewritten or query)

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return "（无历史记录）"
        lines = []
        for turn in history[-5:]:  # 最近5轮
            role = turn.get("role", "user")
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _extract_tag(self, text: str, tag: str) -> str:
        import re
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""
```

### 优先级：**高**——避免每次 NL 都走检索，节省 token

---

## 三、Query Rewriter（查询重写）

### 借鉴来源

**MemU** `src/memu/prompts/retrieve/query_rewriter.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/query_rewriter.py
# 完整文件 L1-45

PROMPT = """
# Task Objective
Rewrite a user query to make it self-contained and explicit by resolving references and ambiguities using the conversation history.

# Workflow
1. Review the **Conversation History** to identify relevant entities, topics, and context.
2. Analyze the **Current Query**.
3. Determine whether the query contains:
   - Pronouns (e.g., "they", "it", "their", "his", "her")
   - Referential expressions (e.g., "that", "those", "the same")
   - Implicit context (e.g., "what about...", "and also...")
   - Incomplete information that can be inferred from the conversation history
4. If rewriting is needed:
   - Replace pronouns with specific entities mentioned in the conversation
   - Add necessary background from the conversation history
   - Make implicit references explicit
   - Ensure the rewritten query is understandable on its own
5. If the query is already clear and self-contained, keep it unchanged.

# Rules
- Preserve the original intent of the user query.
- Only use information explicitly available in the conversation history.
- Do not introduce new assumptions or external knowledge.
- Keep the rewritten query concise but fully explicit.

# Output Format
<analysis>
Brief analysis of whether the query needs rewriting and why.
</analysis>

<rewritten_query>
The rewritten query that is self-contained and explicit if no rewrite is needed).
</rewritten_query>

Query Context:
{conversation_history}

Current Query:
{query}
"""
```

### 星尘改造方案

新增文件：`src/xcmemory_interest/nl/rewriter.py`

```python
# src/xcmemory_interest/nl/rewriter.py

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

class QueryRewriter:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def rewrite(self, query: str, history: list[dict]) -> str:
        formatted_history = self._format_history(history)
        prompt = QUERY_REWRITE_PROMPT.format(
            conversation_history=formatted_history,
            query=query
        )
        response = await self.llm.chat(prompt)
        analysis = self._extract_tag(response, "analysis")
        rewritten = self._extract_tag(response, "rewritten_query")
        return rewritten if rewritten else query
```

### 优先级：**高**——代词解析是 NL 查询质量的关键

---

## 四、Sufficiency Check（检索充分性判断）

### 借鉴来源

**MemU** `src/memu/prompts/retrieve/judger.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/judger.py
# 完整文件 L1-40

PROMPT = """
# Task Objective
Judge whether the retrieved content is sufficient to answer the user's query.

# Workflow
1. Analyze the **Query** to understand what the user is asking.
2. Review the **Retrieved Content** carefully.
3. Evaluate the retrieved content against the following criteria:
   - Does it directly address the user's question?
   - Is the information specific and detailed enough?
   - Are there obvious gaps or missing details?
   - Did the user explicitly ask to recall or remember more information?
4. Based on this evaluation, decide whether the information is sufficient or more is needed.

# Rules
- Base your judgement **only** on the provided query and retrieved content.
- Do not assume or add external knowledge.
- Do not provide additional explanations beyond the required sections.
- The final judgement must be **one word only**.

# Output Format
<consideration>
Explain your reasoning for how you made the judgement.
</consideration>

<judgement>
ENOUGH or MORE
</judgement>

Query:
{query}

Retrieved Content:
{content}
"""
```

### 组合版：Query Rewriter + Judger

**MemU** `src/memu/prompts/retrieve/query_rewriter_judger.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/query_rewriter_judger.py
# 完整文件 L1-49

SYSTEM_PROMPT = """
# Task Objective
Perform two tasks:
1. **Query Rewriting** - Incorporate conversation context to make the query more specific and clear.
2. **Sufficiency Judgment** - Determine whether the retrieved content is sufficient to answer the query.

You should be conservative and only mark the result as **ENOUGH** when the retrieved content truly provides adequate information.

# Rules
- Query rewriting must stay faithful to the user's original intent.
- Only incorporate context that is relevant and helpful.
- Do not introduce new assumptions or external knowledge.
- Mark **ENOUGH** only if:
  - The retrieved content directly addresses the query, **and**
  - The information is specific and detailed enough, **and**
  - There are no obvious gaps or missing details.
- If any key information is missing or unclear, mark **MORE**.

# Output Format
<rewritten_query>
[Provide the rewritten query with conversation context]
</rewritten_query>

<judgement>
ENOUGH or MORE
</judgement>
"""

USER_PROMPT = """
Input:
Query Context:
{conversation_history}

Original Query:
{original_query}

Retrieved Content So Far:
{retrieved_content}
"""
```

### 星尘改造方案

新增文件：`src/xcmemory_interest/nl/sufficiency.py`

```python
# src/xcmemory_interest/nl/sufficiency.py

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

class SufficiencyChecker:
    """判断检索结果是否充分，可能触发扩展检索"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def check(self, query: str, retrieved_content: str) -> tuple[bool, str]:
        """返回 (是否足够, 判断理由)"""
        prompt = SUFFICIENCY_PROMPT.format(query=query, content=retrieved_content)
        response = await self.llm.chat(prompt)
        judgement = self._extract_tag(response, "judgement")
        consideration = self._extract_tag(response, "consideration")
        return (judgement == "ENOUGH", consideration)

    def _extract_tag(self, text: str, tag: str) -> str:
        import re
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""
```

### 优先级：**高**——是检索质量闭环的关键环节

---

## 五、LLM Ranker（LLM 重排序）

### 借鉴来源

**MemU** `src/memu/prompts/retrieve/llm_category_ranker.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/llm_category_ranker.py
# 完整文件 L1-36

PROMPT = """
# Task Objective
Search through the provided categories and identify the most relevant ones for the given query, then rank them by relevance.

# Workflow
1. Analyze the **Query** to understand its intent and key topics.
2. Review all **Available Categories**.
3. Determine which categories are relevant to the query.
4. Select up to **{top_k}** most relevant categories.
5. Rank the selected categories from most to least relevant.

# Rules
- Only include categories that are actually relevant to the query.
- Include **at most** {top_k} categories.
- Ranking matters: the first category must be the most relevant.
- Do not invent or modify category IDs.
- If no categories are relevant, return an empty array.

# Output Format
Return the result as a JSON object in the following format:

```json
{{
  "analysis": "your analysis process",
  "categories": ["category_id_1", "category_id_2", "category_id_3"]
}}
```

Query:
{query}

Available Categories:
{categories_data}
"""
```

**MemU** `src/memu/prompts/retrieve/llm_item_ranker.py` 完整文件

```python
# 文件：memU-main/src/memu/prompts/retrieve/llm_item_ranker.py
# 完整文件 L1-41

PROMPT = """
# Task Objective
Search through the provided memory items and identify the most relevant ones for the given query,
based on the already identified relevant categories, then rank them by relevance.

# Workflow
1. Analyze the **Query** to understand intent and key information needs.
2. Review the **Relevant Categories** provided to understand the scope.
3. Examine all **Available Memory Items** within those categories.
4. Identify which memory items are truly relevant to the query.
5. Select up to **top_k** most relevant items.
6. Rank the selected items from most to least relevant.

# Rules
- Only consider memory items that belong to the provided relevant categories.
- Only include items that are actually relevant to the query.
- Include **at most** {top_k} items.
- Order matters: the first item must be the most relevant.
- Do not invent, modify, or infer item IDs.
- If no relevant items are found, return an empty array.

# Output Format
```json
{{
  "analysis": "your analysis process",
  "items": ["item_id_1", "item_id_2", "item_id_3"]
}}
```

Query:
{query}

Available Memory Items:
{items_data}

These memory items belong to the following relevant categories:
{relevant_categories}
"""
```

### 星尘改造方案

**现状**：星尘的检索是纯向量相似度，没有 LLM 重排序层。

**改造**：在向量初筛结果上，用 LLM 做二次精排：

```python
# src/xcmemory_interest/nl/ranker.py

class MemoryItemRanker:
    """用 LLM 对召回的记忆项进行重排序"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def rank(self, query: str, items: list[dict], top_k: int = 5) -> list[str]:
        """返回排序后的 item_id 列表"""
        prompt = RANKER_PROMPT.format(
            query=query,
            items_data=json.dumps(items, ensure_ascii=False, indent=2),
            top_k=top_k
        )
        response = await self.llm.chat(prompt)
        result = json.loads(self._extract_json(response))
        return result.get("items", [])[:top_k]

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}") + 1
        return text[start:end] if start != -1 else "{}"
```

### 优先级：**中**——向量+LLM 混合是当前最优检索范式

---

## 六、NL → MQL 生成器

### 核心设计（新增能力）

> **规范来源**：本节 Prompt 已整合 `docs/MQL规范.md` 的完整书写规范；完整的 MQL INSERT 约束模板见第十六章。

这是星尘自然语言化的**核心创新点**：让 LLM 学习将 NL 查询翻译为 MQL 语句。

```python
# src/xcmemory_interest/nl/mql_generator.py

NL_TO_MQL_PROMPT = """
# Task Objective
将自然语言查询转换为 MQL (Memory Query Language) 语句。

# MQL 语法参考
SELECT * FROM memories WHERE [slot=value,...] [SEARCH TOPK n] [LIMIT n]
INSERT INTO memories VALUES (query_sentence, content, lifecycle)
UPDATE memories SET field=value WHERE condition
DELETE FROM memories WHERE condition

# 6 个槽位
- subject: 主体（谁）- 谁是动作的执行者或话题主角
- scene: 场景 - 时间点或时间范围 (ISO 格式)
- action: 动作 - 主体做了什么或正在做什么
- object: 宾语 - 动作的对象
- intent: 意图 - 用户的查询意图 (remember/learn/preference/fact)
- emotion: 情感 - 情感倾向 (positive/negative/neutral)

# 操作类型判断
- 回忆具体事实 → SELECT + 可能需要 SEARCH
- 查找偏好习惯 → SELECT + subject + intent
- 查找时间范围 → SELECT + scene 条件
- 查找主题相关 → intent + object

# 示例
NL: "我之前学 Python 时遇到什么问题来着"
MQL: SELECT * FROM memories WHERE subject='我' AND intent='remember' AND object='Python' LIMIT 10

NL: "他对咖啡的偏好是什么"
MQL: SELECT * FROM memories WHERE subject='他' AND intent='preference' AND object='咖啡' LIMIT 5

# 输出格式
<analysis>
查询分析：意图、操作类型、关键槽位
</analysis>

<mql>
生成的 MQL 语句
</mql>

<slots>
推断出的槽位值字典
</slots>

<confidence>
置信度 0.0-1.0
</confidence>

# Input
自然语言查询: {query}
"""

class MQLGenerator:
    """将自然语言查询翻译为 MQL 语句"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def generate(self, nl_query: str) -> dict:
        prompt = NL_TO_MQL_PROMPT.format(query=nl_query)
        response = await self.llm.chat(prompt)
        return {
            "mql": self._extract_tag(response, "mql"),
            "slots": self._parse_json(self._extract_tag(response, "slots")),
            "confidence": float(self._extract_tag(response, "confidence") or "0.5"),
            "operation": self._extract_operation(self._extract_tag(response, "analysis"))
        }

    async def generate_with_fallback(self, nl_query: str) -> dict:
        """置信度低于阈值时降级为纯向量搜索"""
        result = await self.generate(nl_query)
        if result["confidence"] < 0.6:
            return {
                "mql": "SELECT * FROM memories SEARCH TOPK 10",
                "slots": {},
                "confidence": result["confidence"],
                "operation": "hybrid_search",
                "fallback": True
            }
        return result
```

### 优先级：**最高**——整个 NL 系统的核心翻译层

---

## 七、NL → 6槽记忆提取

### 借鉴来源

**MemU** `src/memu/prompts/memory_type/profile.py` L1-191（核心部分）

```python
# 文件：memU-main/src/memu/prompts/memory_type/profile.py
# 行号：56-130（核心 prompt 块）

PROMPT_BLOCK_OBJECTIVE = """
# Task Objective
You are a professional User Memory Extractor. Your core task is to extract
independent user memory items about the user (e.g., basic info, preferences,
habits, other long-term stable traits).
"""

PROMPT_BLOCK_WORKFLOW = """
# Workflow
Read the full conversation to understand topics and meanings.
## Extract memories
Select turns that contain valuable User Information and extract user info memory items.
## Review & validate
Merge semantically similar items.
Resolve contradictions by keeping the latest / most certain item.
## Final output
Output User Information.
"""

PROMPT_BLOCK_RULES = """
# Rules
## General requirements (must satisfy all)
- Use "user" to refer to the user consistently.
- Each memory item must be complete and self-contained, written as a declarative descriptive sentence.
- Each memory item must express one single complete piece of information and be understandable without context.
- Similar/redundant items must be merged into one, and assigned to only one category.
- Each memory item must be < 30 words worth of length (keep it as concise as possible).
- A single memory item must NOT contain timestamps.
Important: Extract only facts directly stated or confirmed by the user. No guesses, no suggestions.
Important: Accurately reflect whether the subject is the user or someone around the user.
Important: Do not record temporary/one-off situational information; focus on meaningful, persistent information.

## Special rules for User Information
- Any event-related item is forbidden in User Information.
- Do not extract content that was obtained only through the model's follow-up questions unless the user shows strong proactive intent.

## Forbidden content
- Knowledge Q&A without a clear user fact.
- Trivial updates that do not add meaningful value.
- Turns where the user did not respond and only the assistant spoke.
- Illegal / harmful sensitive topics.
- Any content mentioned only by the assistant and not explicitly confirmed by the user.
"""

PROMPT_BLOCK_OUTPUT = """
# Output Format (XML)
Return all memories wrapped in a single <item> element:
<item>
    <memory>
        <content>User memory item content 1</content>
        <categories>
            <category>Category Name</category>
        </categories>
    </memory>
</item>
"""

PROMPT_BLOCK_EXAMPLES = """
# Examples (Input / Output / Explanation)
Example 1: User Information Extraction
## Input
user: ... (对话内容)
## Output
<item>
    <memory>
        <content>The user works as a product manager at an internet company</content>
        <categories>
            <category>Basic Information</category>
        </categories>
    </memory>
</item>
## Explanation
Only stable user facts explicitly stated by the user are extracted.
"""
```

**MemU** `src/memu/prompts/memory_type/event.py` L57-172（Event 类型）

```python
# 文件：memU-main/src/memu/prompts/memory_type/event.py
# 行号：57-172

# Event 与 Profile 的主要区别：
# - Event 关注特定时间发生的事件、活动、经历
# - Event 必须包含时间、地点、参与者等细节
# - Event 每条 < 50 词
# - Event 禁止行为模式、习惯、偏好（这些归 Profile）
```

### 星尘改造方案（对齐 MQL规范.md）

> **规范来源**：`docs/MQL规范.md`；slot 定义与 MQL INSERT 语法完全对齐，确保 NL→槽提取的输出可直接用于 MQL INSERT。

```python
# src/xcmemory_interest/nl/slot_extractor.py

NL_TO_SLOTS_PROMPT = """
# Task Objective
从自然语言文本中提取记忆的 6 槽结构，输出严格遵循 MQL 书写规范。

# 6 槽定义（与 MQL规范.md 一致）
- subject: 主体 - 谁是主角？
- scene: 场景 - 记忆发生的场景，包括时间场景和空间场景。只用预定义场景词之一：
  - 时间场景：<平时>/<少年期>/<童年>/<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<周末>/<假期>/<本周早些时候>/<YYYY-MM-DD>
  - 空间场景：<家里>/<公司>/<学校>/<户外>/<线上>/<路上>
- action: 动作 - 严格使用预定义动词：<是>/<有>/<与>/<的>/<叫>/<差>/<来自>/<喜欢>/<知道>/<不知道>/<同意>/<拒绝>/<希望>/<遵循>/<发生于>/<发生>/<想>/<说>/<做>。无法匹配时用最接近的单字动词
- object: 宾语 - action 的直接承受者
- ★purpose: 语义类别——这条记忆描述什么维度？（名字/身份/关系/年龄差距/喜好/经历/计划/密码等）
- ★result: 具体值或结论——对应 purpose 问题的答案

# 规则
- **★每个槽位只写一个词，不写短句★**（核心规则）
  错误：object=<被父亲交给哥哥照顾> — 这是句子不是词
  正确：拆为 action=<做>, object=<照顾>, purpose=<照顾者>, result=<哥哥>
- 六槽必须等长，缺槽用 <无> 占位
- 只提取明确提到的信息，不过度推断
- subject 默认为"我"如果未指明
- lifecycle 推断：<平时>/<少年期>/<童年> → 999999；<本周早些时候>/<周末>/<假期> → 604800；<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<家里>/<公司>/<学校>/<户外>/<线上>/<路上> → 86400
- 单一事实：一条记忆只表达一个独立事实
- description 不重复六槽已有信息

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
"""

class SlotExtractor:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def extract(self, nl_text: str) -> dict:
        prompt = NL_TO_SLOTS_PROMPT.format(text=nl_text)
        response = await self.llm.chat(prompt)
        return {
            "slots": self._parse_json(self._extract_tag(response, "slots")),
            "description": self._extract_tag(response, "description"),
            "lifecycle": int(self._extract_tag(response, "lifecycle") or "86400")
        }
```

### 优先级：**高**——写入路径的核心

---

## 八、记忆分类（Category）与摘要

### 借鉴来源

**MemU** `src/memu/prompts/category_summary/category.py` L1-297（核心部分）

```python
# 文件：memU-main/src/memu/prompts/category_summary/category.py
# 行号：1-143（PROMPT_LEGACY 部分）

# Category Summary Prompt 的核心功能：
# 1. 将新的 memory items 合并到已有分类摘要中
# 2. 处理 Add（新增）和 Update（更新已有）两种操作
# 3. 基于 memory_type 决定冲突解决策略
# 4. 合并相似条目，去重
# 5. 输出 Markdown 格式的分类摘要
```

**MemU** `src/memu/app/memorize.py` L648-668（Category 初始化）

```python
# 行号：648-668

async def _initialize_categories(
    self, ctx: Context, store: Database, user: Mapping[str, Any] | None = None
) -> None:
    if ctx.categories_ready:
        return
    if not self.category_configs:
        ctx.categories_ready = True
        return
    cat_texts = [self._category_embedding_text(cfg) for cfg in self.category_configs]
    cat_vecs = await self._get_llm_client("embedding").embed(cat_texts)
    ctx.category_ids = []
    ctx.category_name_to_id = {}
    for cfg, vec in zip(self.category_configs, cat_vecs, strict=True):
        name = cfg.name.strip() or "Untitled"
        description = cfg.description.strip()
        cat = store.memory_category_repo.get_or_create_category(
            name=name, description=description, embedding=vec, user_data=dict(user or {})
        )
        ctx.category_ids.append(cat.id)
        ctx.category_name_to_id[name.lower()] = cat.id
    ctx.categories_ready = True
```

### 星尘改造方案

**现状**：星尘没有 Category 抽象层，所有记忆平铺管理。

**方案**：
- 短期：不需要 Category，但可以为每个记忆关联 `memory_type`（fact/preference/habit/event/skill/goal）
- 中期：参考 MemU 实现 Category + CategorySummary，支持分类聚合查询

### 优先级：**中**

---

## 九、混合检索策略（Hybrid Search）

### 借鉴来源

**Text2Mem** 检索策略：

```python
# 参考 text2mem 中的检索实现
final_sim = alpha * semantic_score + beta * keyword_score + phrase_bonus
```

MemU 支持两种检索模式：
- **RAG 模式**（快）：纯向量相似度
- **LLM 模式**（深）：LLM 驱动的重排序

### 星尘改造方案

**现状**：星尘只有 Chroma 向量检索。

**改造**：在向量结果上叠加关键词匹配 rerank：

```python
# src/xcmemory_interest/nl/hybrid_search.py

class HybridSearch:
    """
    混合检索：向量相似度 × 关键词匹配 × 短语精确奖励

    公式: final_score = α × vector_sim + β × keyword_score + phrase_bonus
    """

    def __init__(self, memory_system, alpha: float = 0.7, beta: float = 0.3):
        self.mem = memory_system
        self.alpha = alpha
        self.beta = beta

    async def search(self, query: str, top_k: int = 10) -> list[dict]:
        # 1. 向量检索
        vector_results = await self.mem.search(query, top_k=top_k * 2)

        # 2. 关键词检索
        keywords = self._extract_keywords(query)
        keyword_matches = self._filter_by_keywords(vector_results, keywords)

        # 3. 混合打分
        scored = []
        for item in vector_results:
            vec_score = item.get("score", 0.0)
            kw_score = keyword_matches.get(item["id"], 0.0)
            phrase_bonus = self._phrase_bonus(query, item["content"])
            final = self.alpha * vec_score + self.beta * kw_score + phrase_bonus
            scored.append((item, final))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored[:top_k]]

    def _extract_keywords(self, query: str) -> set[str]:
        # 简单分词 + 停用词过滤
        import jieba
        stopwords = {"的", "了", "是", "在", "我", "有", "和", "就"}
        words = jieba.lcut(query)
        return {w for w in words if len(w) > 1 and w not in stopwords}

    def _phrase_bonus(self, query: str, content: str) -> float:
        # 短语精确匹配奖励
        query_lower = query.lower()
        content_lower = content.lower()
        # 检查是否有连续 n-gram 匹配
        return 0.1 if query_lower in content_lower else 0.0
```

### 优先级：**高**

---

## 十、Workflow Step 编排引擎

### 借鉴来源

**MemU** `src/memu/workflow/step.py` L1-102 完整文件

```python
# 文件：memU-main/src/memu/workflow/step.py
# 完整文件 L1-102

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

WorkflowState = dict[str, Any]
WorkflowContext = Mapping[str, Any] | None
WorkflowHandler = Callable[[WorkflowState, WorkflowContext], Awaitable[WorkflowState] | WorkflowState]

@dataclass
class WorkflowStep:
    step_id: str
    role: str
    handler: WorkflowHandler
    description: str = ""
    requires: set[str] = field(default_factory=set)   # 前置 state keys
    produces: set[str] = field(default_factory=set)   # 输出 state keys
    capabilities: set[str] = field(default_factory=set)  # llm/vector/db/io/vision
    config: dict[str, Any] = field(default_factory=dict)

    async def run(self, state: WorkflowState, context: WorkflowContext) -> WorkflowState:
        result = self.handler(state, context)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping):
            msg = f"Workflow step '{self.step_id}' must return a mapping"
            raise TypeError(msg)
        return dict(result)

async def run_steps(
    name: str,
    steps: list[WorkflowStep],
    initial_state: WorkflowState,
    context: WorkflowContext = None,
    interceptor_registry: WorkflowInterceptorRegistry | None = None,
) -> WorkflowState:
    """顺序执行所有步骤，支持拦截器钩子"""
    state = dict(initial_state)
    for step in steps:
        missing = step.requires - state.keys()
        if missing:
            msg = f"Workflow '{name}' missing required keys: {', '.join(sorted(missing))}"
            raise KeyError(msg)
        # before interceptors
        # execute step.run(state, context)
        # after interceptors / on_error interceptors
    return state
```

### 星尘改造方案

**现状**：星尘的 `memorize()` / `retrieve()` 是线性过程，没有步骤编排抽象。

**方案**：短期不引入完整工作流引擎，但在 NL 模块中模拟类似模式：

```python
# src/xcmemory_interest/nl/pipeline.py

class NLSearchPipeline:
    """
    NL 检索流水线（简化版 WorkflowStep 模式）
    """

    STEPS = [
        ("pre_decision", "预检索判断"),
        ("rewrite", "查询重写"),
        ("nl_to_mql", "NL→MQL生成"),
        ("execute_mql", "执行MQL"),
        ("sufficiency_check", "充分性检查"),
        ("hybrid_rerank", "混合重排"),
    ]

    def __init__(self, llm_client, memory_system):
        self.llm = llm_client
        self.mem = memory_system
        self.decider = NLQueryDecider(llm_client)
        self.rewriter = QueryRewriter(llm_client)
        self.mql_gen = MQLGenerator(llm_client)
        self.sufficiency = SufficiencyChecker(llm_client)
        self.hybrid = HybridSearch(memory_system)

    async def run(self, nl_query: str, history: list[dict]) -> dict:
        state = {"query": nl_query, "history": history}

        # Step 1: 预检索判断
        need_retrieve, state["query"] = await self.decider.decide(state["query"], history)
        if not need_retrieve:
            return {"type": "direct", "response": state["query"]}

        # Step 2: 查询重写
        state["query"] = await self.rewriter.rewrite(state["query"], history)

        # Step 3: NL→MQL
        mql_plan = await self.mql_gen.generate_with_fallback(state["query"])
        state["mql"] = mql_plan["mql"]

        # Step 4: 执行 MQL
        result = self._exec_mql(state["mql"])
        state["result"] = result

        # Step 5: 充分性检查
        sufficient, reason = await self.sufficiency.check(
            state["query"], self._format_result(result)
        )
        if not sufficient:
            # 扩展检索
            extended = await self.hybrid.search(state["query"], top_k=10)
            state["result"] = self._merge_results(result, extended)

        return {
            "type": "retrieved",
            "mql": state["mql"],
            "result": state["result"],
            "rewritten_query": state["query"]
        }
```

### 优先级：**中**——可逐步演进，不影响当前架构

---

## 十一、Tool Memory（工具记忆）

### 借鉴来源

**MemU** `src/memu/database/models.py` L43-66（ToolCallResult 模型）

```python
# 文件：memU-main/src/memu/database/models.py
# 行号：43-66

class ToolCallResult(BaseModel):
    """Represents the result of a tool invocation for Tool Memory."""

    tool_name: str = Field(..., description="Name of the tool that was called")
    input: dict[str, Any] | str = Field(default="", description="Tool input parameters")
    output: str = Field(default="", description="Tool output result")
    success: bool = Field(default=True, description="Whether the tool invocation succeeded")
    time_cost: float = Field(default=0.0, description="Time consumed by the tool invocation in seconds")
    token_cost: int = Field(default=-1, description="Token consumption of the tool")
    score: float = Field(default=0.0, description="Quality score from 0.0 to 1.0")
    call_hash: str = Field(default="", description="Hash of input+output for deduplication")
    created_at: datetime = Field(default_factory=lambda: pendulum.now("UTC"))

    def generate_hash(self) -> str:
        """Generate MD5 hash from tool input and output for deduplication."""
        input_str = json.dumps(self.input, sort_keys=True) if isinstance(self.input, dict) else str(self.input)
        combined = f"{self.tool_name}|{input_str}|{self.output}"
        return hashlib.md5(combined.encode("utf-8"), usedforsecurity=False).hexdigest()

    def ensure_hash(self) -> None:
        """Ensure call_hash is set, generate if empty."""
        if not self.call_hash:
            self.call_hash = self.generate_hash()
```

**MemU** `src/memu/database/sqlite/repositories/memory_item_repo.py` L246-253（tool_record 处理）

```python
# 行号：246-253

# Build extra dict with tool_record fields at top level
extra: dict[str, Any] = {}
if tool_record:
    if tool_record.get("when_to_use") is not None:
        extra["when_to_use"] = tool_record["when_to_use"]
    if tool_record.get("metadata") is not None:
        extra["metadata"] = tool_record["metadata"]
    if tool_record.get("tool_calls") is not None:
        extra["tool_calls"] = tool_record["tool_calls"]
```

### 星尘改造方案

**现状**：星尘的 `tool_calls` 表没有记忆追踪机制。

**方案**：
- 在 MemoryItem.extra 中增加 `tool_calls` 字段（类型：list[ToolCallResult] 序列化）
- `tool_name` + `input` + `output` 的 MD5 作为 `call_hash`
- 支持按 `tool_name` 查询工具调用历史
- 用于记录 agent 调用工具的成功率和使用模式

### 优先级：**中**

---

## 十二、STO 阶段操作集（Text2Mem 借鉴）

### 借鉴来源

**Text2Mem** IR 结构：`{stage, op, target, args, meta}`

| Stage | Op | 星尘对应 |
|-------|-----|---------|
| ENC | Encode | `memorize()` |
| RET | Retrieve, Summarize | `retrieve()` |
| STO | Update, Label, **Promote**, **Demote**, **Merge**, **Split**, **Delete**, **Lock**, **Expire** | 缺失或弱实现 |

### 星尘缺失的 STO 操作

#### 12.1 Promote / Demote（权重调整）

```python
# 伪代码示例
# MQL: PROMOTE memory_id [weight_delta=0.2]
# MQL: DEMOTE memory_id [weight_delta=0.1]

async def promote(self, memory_id: str, weight_delta: float = 0.2) -> bool:
    """提升记忆权重（增加 access_count 的影响因子）"""
    item = self.get(memory_id)
    if not item:
        return False
    current_weight = item.extra.get("importance_weight", 1.0)
    item.extra["importance_weight"] = current_weight + weight_delta
    item.updated_at = pendulum.now()
    return True
```

#### 12.2 Expire（过期机制）

```python
# MQL: EXPIRE memory_id AFTER 30d

# 在 MemoryItem.extra 中增加
# {
#     "expires_at": "2026-05-19T00:00:00Z",  # ISO 时间戳
#     "auto_delete": true
# }

async def expire_after(self, memory_id: str, days: int) -> bool:
    """设置记忆在 N 天后过期"""
    item = self.get(memory_id)
    if not item:
        return False
    expires_at = pendulum.now().add(days=days)
    item.extra["expires_at"] = expires_at.isoformat()
    item.extra["auto_delete"] = True
    return True
```

#### 12.3 Lock（锁定防误删）

```python
# MQL: LOCK memory_id
# MQL: UNLOCK memory_id

async def lock(self, memory_id: str) -> bool:
    """锁定记忆，防止误删"""
    item = self.get(memory_id)
    if not item:
        return False
    item.extra["locked"] = True
    return True

# DELETE 时检查 locked
async def delete(self, memory_id: str) -> bool:
    item = self.get(memory_id)
    if item and item.extra.get("locked"):
        raise PermissionError(f"Memory {memory_id} is locked")
    # ... 执行删除 ...
```

#### 12.4 Merge / Split（合并/拆分）+ Lineage 追踪

**Text2Mem** 的 lineage 字段：

```sql
-- Text2Mem 的 lineage 字段
lineage_parents TEXT,  -- JSON array of ancestor IDs
lineage_children TEXT, -- JSON array of descendant IDs
```

```python
# MemoryItem.extra 中增加
# {
#     "lineage_parents": ["id1", "id2"],   # 合并前的原始记忆 ID
#     "lineage_children": ["id4"],          # 拆分后的子记忆 ID
# }

async def merge(self, memory_ids: list[str], merged_content: str) -> str:
    """合并多条记忆为一条，保留血缘关系"""
    # 1. 创建新记忆
    new_id = self.write(merged_content)
    # 2. 设置 lineage_parents
    self.update(new_id, extra={"lineage_parents": memory_ids})
    # 3. 被合并的旧记忆标记为 deprecated
    for mid in memory_ids:
        self.update(mid, extra={"deprecated": True, "replaced_by": new_id})
    return new_id
```

### 优先级：**中**——属于增强功能，可分期实现

---

## 十三、Dry-run 模式

### 借鉴来源

**Text2Mem** 的 `dry_run` 机制：

```json
{
  "meta": {"dry_run": true},
  "op": "Delete",
  "target": {"all": true}
}
```

**MemU** `src/memu/workflow/pipeline.py` 中也有类似的前置检查：

```python
# 伪代码参考
if self.target.all:
    if not (self.meta.dry_run or self.meta.confirmation):
        raise ValueError("target.all requires dry_run or confirmation")
```

### 星尘改造方案

**现状**：星尘的 `DELETE FROM memories WHERE ...` 直接执行，没有预览机制。

**改造**：

```python
# MQL: DELETE FROM memories WHERE time < '2024-01-01' DRYRUN

class InterpreterExtended:
    def _execute_delete(self, stmt: DeleteStatement) -> QueryResult:
        dry_run = stmt.kwargs.get("dry_run", False)

        # 先预览影响的行
        preview_result = self._preview_delete(stmt)
        preview_result.message = f"将删除 {preview_result.affected_rows} 条记忆（dry_run）"

        if dry_run:
            return preview_result

        # 实际执行
        return self._do_delete(stmt)
```

### 优先级：**中**——安全增强功能

---

## 十四、相对时间过滤器

### 借鉴来源

**Text2Mem** 的时间范围过滤器：

```python
# Text2Mem 支持 "last 7 days" 相对时间
TimeRange(relative="last", amount=7, unit="days")
```

### 星尘改造方案

**现状**：星尘 MQL 的 `WHERE time > '2024-01-01'` 只支持绝对时间。

**改造**：

```python
# MQL 支持相对时间：
# WHERE time > last_7_days
# WHERE time < last_month
# WHERE time >= last_3_hours

RELATIVE_TIME_MAP = {
    "last_5_minutes": 5 * 60,
    "last_15_minutes": 15 * 60,
    "last_1_hour": 3600,
    "last_3_hours": 3 * 3600,
    "last_24_hours": 24 * 3600,
    "last_7_days": 7 * 86400,
    "last_30_days": 30 * 86400,
    "last_3_months": 90 * 86400,
    "last_1_year": 365 * 86400,
}

def _parse_time_condition(self, time_str: str) -> datetime | None:
    """解析时间条件，支持相对时间"""
    if time_str in RELATIVE_TIME_MAP:
        seconds = RELATIVE_TIME_MAP[time_str]
        return pendulum.now().subtract(seconds=seconds)
    # 否则按 ISO 格式解析
    return pendulum.parse(time_str)
```

### 优先级：**中**——用户体验提升

---

## 十五、Facets 结构（Text2Mem 借鉴）

### 借鉴来源

**Text2Mem** Facets（四字段：subject/scene/location/topic）

| Text2Mem Facets | 星尘 6 槽 |
|-----------------|----------|
| subject（谁） | 主体 (who) |
| scene（何时） | 时间 (when) |
| location（地点） | **缺失** |
| topic（主题） | 意图 (intent) |

### 星尘改造方案

**现状**：星尘的 6 槽中没有 `location`。

**改造**：在 6 槽中增加 `location` 槽：

```
<scene><subject><action><object><purpose><result>_<location>
```

示例：`"<平时><我><学习><Python><提升><成长>_<北京>"
```

### 优先级：**低**——锦上添花

---

## 十六、MQL 书写规范（整合自 MQL规范.md）

> **来源**：`docs/MQL规范.md`（星尘项目组编写）
> **用途**：为 NL→MQL 生成器提供确定性书写规则，是 LLM 翻译的自然语言查询的格式约束。

### 16.1 基本结构

```sql
INSERT INTO memories VALUES ('<scene><subject><action><object><purpose><result>', 'description', lifecycle);
```

| 参数 | 格式 | 作用 |
|------|------|------|
| `query_sentence` | 六槽 `<>` 包裹字符串 | 查询时的匹配依据 |
| `description` | 自然语言描述 | 六槽内容的语义解释，不重复六槽已有信息 |
| `lifecycle` | 整数（秒） | 记忆有效期 |

### 16.2 六槽详解

```
<scene><subject><action><object><purpose><result>
 ①      ②        ③       ④       ⑤        ⑥
```

**① scene 槽——场景标签**（时间场景+空间场景），subject 不能混入 scene 槽：

**时间场景**：

| 时间词 | 语义 | lifecycle |
|--------|------|-----------|
| `<平时>` | 永久事实、习惯性状态、角色设定 | 999999 |
| `<少年期>` | 12-15 岁期间的过去事件 | 999999 |
| `<童年>` | 幼年时期 | 999999 |
| `<那天晚上>` | 某次具体事件（当晚发生的事） | 86400 |
| `<深夜>` | 深夜时段发生的事 | 86400 |
| `<早上>` | 早上时段发生的事 | 86400 |
| `<晚上>` | 泛指晚上的事 | 86400 |
| `<白天>` | 泛指白天的事 | 86400 |
| `<周末>` | 周末发生的事 | 604800 |
| `<假期>` | 假期发生的事 | 604800 |
| `<本周早些时候>` | 本周内的事件 | 604800 |
| `<2026-04-17>` | 具体日期 | 按重要性 |

**空间场景**：

| 场景词 | 语义 | lifecycle |
|--------|------|-----------|
| `<家里>` | 家庭场景 | 86400 |
| `<公司>` | 工作场景 | 86400 |
| `<学校>` | 学习场景 | 86400 |
| `<户外>` | 户外场景 | 86400 |
| `<线上>` | 网络/线上场景 | 86400 |
| `<路上>` | 通行场景 | 86400 |

> **禁止**：`<近日>` `<最近>` `<前些时候>` 等未约定词汇。
> 当文本同时暗示时间和空间时，优先选最突出的那个场景维度填入 scene 槽。

**③ action 槽——决定后续槽位如何填充**：

| action | 语义 | 填充模式 |
|--------|------|----------|
| `<是>` | 定义身份/类型 | `<scene><subject><是><属性><身份/类别><具体值>` |
| `<有>` | 拥有/存在 | `<scene><subject><有><对象><关系><具体值>` |
| `<与>` | 描述两方关系 | `<scene><subject><与><另一方><关系类型><具体说明>` |
| `<的>` | 归属/属性 | `<scene><subject><的><属性名><类别><属性值>` |
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

**④ object 槽**——action 的直接宾语。
**⑤ purpose 槽**——语义类别。描述本条记忆回答什么类型的问题（名字/身份/关系/年龄差距/喜好/经历/计划/密码等）。填入一个类别词。
**⑥ result 槽**——具体值或结论。对应 purpose 的答案。填入一个值词。

**★每个槽位只写一个词，不写短句★**：槽位是关键词索引，不是叙述文本。多词信息拆分到不同槽位。

### 16.3 lifecycle 分配规则

| 数值 | 秒数 | 用途 |
|------|------|------|
| 999999 | 永久 | 身份、性格、角色设定、扮演规则、长期关系 |
| 604800 | 一周 | 本周早些时候的事件 |
| 86400 | 一天 | 日常互动、临时对话 |

**lifecycle 与 scene 槽语义必须一致**：`<平时>/<少年期>/<童年>` → 999999；`<本周早些时候>/<周末>/<假期>` → 604800；`<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<家里>/<公司>/<学校>/<户外>/<线上>/<路上>` → 86400。

### 16.4 六槽等长原则

六槽必须严格等长，缺槽用 `<无>` 占位，不能多也不能少。

```sql
-- ✅ 正确：六个槽位完整，每个槽只一个词（推荐用 purpose + result 拆开语义类别和具体值）
INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', 2592000);
INSERT INTO memories VALUES ('<所有><星织><有><哥哥><关系><绯绯>', '星织有个哥哥叫绯绯', 2592000);

-- ❌ 错误：少一个槽
INSERT INTO memories VALUES ('<平时><星织><是><女性><无>', '星织是女性', 999999);

-- ❌ 错误：多一个槽
INSERT INTO memories VALUES ('<平时><星织><是><女性><无><无><无>', '星织是女性', 999999);
```

### 16.5 常见错误汇总

| # | 错误类型 | ❌ 示例 | ✅ 正确 |
|---|---------|--------|--------|
| 1 | scene 槽塞入 subject | `'<平时><早上><哥哥醒来>...'` | `'<早上><哥哥><醒来>...'` |
| 2 | action 包含完整动作 | `'<平时><星织><同意与哥哥><发展感情>...'` | `'<深夜><星织><同意><哥哥><发展感情><慢慢来>'` |
| 3 | 用 `<是>` 描述关系 | `'<平时><星织><是><绯绯妹妹><无><无>'` | `'<平时><星织><与><绯绯><血缘关系><同父异母>'` |
| 4 | lifecycle 与 scene 不一致 | `'<本周早些时候>...', 999999)` | `'<本周早些时候>...', 604800)` |

### 16.6 单一事实原则

一条记忆只表达一个独立事实。如果能用一个查询词召回多个事实，就必须拆成多条。

```sql
-- ✅ 正确：关系拆分，每个主体一条
INSERT INTO memories VALUES ('<平时><绯绯><希望><星织><发展><恋人>', '绯绯希望星织发展成恋人关系', 999999);
INSERT INTO memories VALUES ('<深夜><星织><同意><绯绯><节奏><慢慢来>', '星织同意与绯绯发展关系，但要求慢慢来', 999999);

-- ❌ 错误：合并成一条
INSERT INTO memories VALUES ('<平时><绯绯><希望星织发展恋人关系但星织要求慢慢来><无><无><无>', '绯绯希望星织发展成恋人关系...', 999999);
```

### 16.7 对 NL→MQL 生成器的约束

在第六节的 `NL_TO_MQL_PROMPT` 中，LLM 输出 INSERT 语句时必须遵循以上全部规则。NL→MQL 生成器的完整 Prompt 应在系统提示中内嵌本节关键约束：

```
# MQL INSERT 约束（违反则输出无效）
- 格式：INSERT INTO memories VALUES ('<scene><subject><action><object><purpose><result>', 'description', lifecycle)
- 六槽必须等长，缺槽用 <无> 占位
- action 只用预定义动词：<是>/<有>/<与>/<的>/<叫>/<差>/<来自>/<喜欢>/<知道>/<不知道>/<同意>/<拒绝>/<希望>/<遵循>/<发生于>/<发生>/<想>/<说>/<做>
- **每个槽位只写一个词，不写短句**。槽位是关键词索引，多词信息拆分到不同槽位
- scene 只用预定义场景词：
  - 时间场景：<平时>/<少年期>/<童年>/<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<周末>/<假期>/<本周早些时候>/<YYYY-MM-DD>
  - 空间场景：<家里>/<公司>/<学校>/<户外>/<线上>/<路上>
- lifecycle 规则：<平时>/<少年期>/<童年> → 999999；<本周早些时候>/<周末>/<假期> → 604800；<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<家里>/<公司>/<学校>/<户外>/<线上>/<路上> → 86400
- 单一事实：一条记忆只表达一个独立事实
- description 不重复六槽已有信息
```

---

## 附录：文件变更清单

| 新增/改造 | 文件路径 | 职责 |
|---------|---------|------|
| **新增** | `src/xcmemory_interest/nl/__init__.py` | NL 模块导出 |
| **新增** | `src/xcmemory_interest/nl/decision.py` | 预检索判断 |
| **新增** | `src/xcmemory_interest/nl/rewriter.py` | 查询重写 |
| **新增** | `src/xcmemory_interest/nl/sufficiency.py` | 充分性检查 |
| **新增** | `src/xcmemory_interest/nl/mql_generator.py` | NL→MQL 翻译（核心） |
| **新增** | `src/xcmemory_interest/nl/slot_extractor.py` | NL→6槽提取 |
| **新增** | `src/xcmemory_interest/nl/hybrid_search.py` | 混合检索 |
| **新增** | `src/xcmemory_interest/nl/ranker.py` | LLM 重排序 |
| **新增** | `src/xcmemory_interest/nl/pipeline.py` | NL 流水线编排 |
| **新增** | `src/xcmemory_interest/nl/system.py` | NL 记忆系统入口 |
| **改造** | `src/xcmemory_interest/memory_system.py` | 增加 reinforce / content_hash 逻辑 |
| **改造** | `src/xcmemory_interest/mql/interpreter_extended.py` | 增加 PROMOTE/DEMOTE/EXPIRE/LOCK 操作 |
| **改造** | `src/xcmemory_interest/models.py` | MemoryItem.extra 增加强化追踪字段 |
| **改造** | `docs/MQL_REFERENCE.md` | 补充 STO 操作语法 |

---

## 优先级汇总

| 优先级 | 功能 | 来源 |
|-------|------|------|
| ★★★ | NL→MQL 生成器（已整合 MQL规范.md） | 新设计 |
| ★★★ | 去重 + Reinforcement | MemU |
| ★★★ | Pre-Retrieval Decision | MemU |
| ★★★ | Query Rewriter | MemU |
| ★★★ | Sufficiency Check | MemU |
| ★★ | Hybrid Search | Text2Mem+MemU |
| ★★ | NL→6槽记忆提取（已对齐 MQL规范.md） | MemU |
| ★★ | Salience Ranking | MemU |
| ★★ | MQL 书写规范（整合自 MQL规范.md） | 星尘 |
| ★ | LLM Ranker | MemU |
| ★ | Tool Memory | MemU |
| ★ | STO 操作集（P/D/E/L） | Text2Mem |
| ★ | Dry-run 模式 | Text2Mem |
| ★ | 相对时间过滤器 | Text2Mem |
| ☆ | Category + 摘要合并 | MemU |
| ☆ | Facets(location) | Text2Mem |
