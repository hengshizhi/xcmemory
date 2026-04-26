# 星尘记忆 Chat — 开发文档

> 类酒馆角色扮演对话应用，集成星辰记忆数据库，支持自白过程的记忆触发。

---

## 一、产品定位

一个**独立的角色扮演对话程序**，核心特色是「自白过程 + 记忆系统」的深度集成。角色在内心对话（自白）中可以主动回忆和记住信息，记忆系统在自白流程中实时介入，使角色拥有真正的"记忆"能力。

### 与酒馆（SillyTavern）的对比

| 维度 | 酒馆 | 星尘记忆 Chat |
|------|------|--------------|
| 记忆 | World Info / Lorebook（静态关键词触发） | 动态记忆系统（语义搜索 + 生命周期） |
| 角色内在过程 | 无 | 自白过程（内心对话 + 记忆触发） |
| 记忆写入 | 手动 | 自白中「记住」自动写入 |
| 记忆召回 | 关键词匹配 | 语义向量搜索 + MQL |

---

## 二、架构概览

```
chat/                          # 独立程序根目录
├── DESIGN.md                  # 本文档
├── config.toml                # 配置文件（API地址、Key、模型等）
├── main.py                    # 入口：启动 Chat
├── character_card.py          # 角色卡加载与解析
├── chat_engine.py             # 对话引擎（核心：自白流程 + 记忆集成）
├── memory_client.py           # 星辰记忆 HTTP API 客户端
├── llm_client.py              # OpenAI 兼容 LLM 客户端
├── ui/                        # UI 层（可替换）
│   └── terminal_ui.py         # 终端 UI（MVP 版本）
├── characters/                # 角色卡存放目录
│   └── example.yaml           # 示例角色卡
└── requirements.txt           # 依赖
```

### 依赖关系

```
main.py
  ├── ui/terminal_ui.py          # 用户交互
  ├── chat_engine.py             # 对话编排
  │   ├── llm_client.py          # OpenAI API 调用
  │   ├── memory_client.py       # 星辰记忆 API 调用
  │   └── character_card.py      # 角色卡解析
  └── config.toml               # 配置
```

---

## 三、配置文件设计

`config.toml`:

```toml
# ── 星辰记忆数据库 ──
[memory]
# HTTP API 地址
base_url = "http://127.0.0.1:8080"
# API Key（从星辰记忆服务器获取）
api_key = "xi-admin-xxxx"
# 记忆系统名称
system_name = "default"

# ── LLM（OpenAI 兼容）──
[llm]
# OpenAI / DeepSeek / OpenRouter 等
base_url = "https://api.deepseek.com"
api_key = "sk-xxxx"
model = "deepseek-v4-flash"
# 对话最大 token
max_tokens = 2048
temperature = 0.8

# ── 自白设置 ──
[monologue]
# 触发回忆的关键词列表
recall_triggers = ["回忆一下", "回忆", "记得", "记忆中", "之前"]
# 触发记忆写入的关键词列表
remember_triggers = ["记住", "记住这个", "铭记", "记下来", "要记得"]
# 自白最大段数（防止无限循环）
max_segments = 20
# 记忆召回的 top_k
recall_top_k = 5

# ── 角色卡 ──
[character]
# 默认角色卡路径（相对于 characters/ 目录）
default = "example"
```

---

## 四、角色卡设计

角色卡是 YAML 文件，存放在 `characters/` 目录下。

### 角色卡结构

```yaml
# characters/example.yaml

# 基本信息
name: "星织"
avatar: "🌟"  # 终端 UI 用

# 自我介绍（注入 system prompt）
introduction: |
  我是星织，一个在深夜里思考人生的女孩。
  我喜欢在安静的夜晚整理思绪，偶尔会回忆过去的经历。
  我对感情很认真，但有时候会犹豫不决。

# 意识设定（注入 system prompt，决定角色的思维方式和行为模式）
consciousness: |
  - 我的内心对话（自白）是分段的，每段用换行分隔
  - 我可以在自白中用「回忆一下」来主动回忆过去的记忆
  - 我可以在自白中用「记住」来决定记住某些重要的事
  - 我的自白会先于我的回复出现，代表我的内心活动过程
  - 回忆结果会作为我自白的一部分，影响我的后续思考
  - 我会在回忆后继续思考，然后给出回复

# 性格标签（用于记忆系统 holder 匹配）
personality_tags:
  - "内向"
  - "感性"
  - "认真"

# 对话风格
dialogue_style:
  tone: "温柔但有时犹豫"
  habits:
    - "说话前会先想一下"
    - "对重要的事会反复确认"
```

### 角色卡加载

```python
# character_card.py
class CharacterCard:
    name: str
    avatar: str
    introduction: str
    consciousness: str
    personality_tags: list[str]
    dialogue_style: dict

    @classmethod
    def load(cls, path: str) -> "CharacterCard": ...
```

---

## 五、核心流程：自白过程

### 5.1 自白是什么

自白是角色的**内心对话**，以换行分段。在最终回复之前，角色会先经过内心思考过程，这个过程中可以触发记忆操作。

### 5.2 自白流程

```
用户输入
  ↓
[构建 Prompt]
  角色卡 + 对话历史 + 记忆上下文 + 自白指令
  ↓
[LLM 生成自白]（streaming）
  ↓ 逐段输出
  ┌─────────────────────────────┐
  │ 段落 1: "我在想这件事..."    │  → 普通段落，直接显示
  │ 段落 2: "回忆一下上次..."    │  → 检测到回忆触发词
  │   ↓                         │
  │ [暂停输出]                   │
  │ [调用记忆系统: NL查询]       │
  │ [获得记忆结果]               │
  │ [注入记忆结果作为下一段]     │  → "（回忆起：上次和绯绯去看了电影...）"
  │ [继续输出]                   │
  │ 段落 3: "原来如此，那..."    │  → 继续自白
  │ 段落 4: "记住我明天要开会"   │  → 检测到记忆写入触发词
  │   ↓                         │
  │ [暂停输出]                   │
  │ [调用记忆系统: NL写入]       │
  │ [获得写入结果]               │
  │ [注入确认作为下一段]         │  → "（已记住：明天要开会）"
  │ [继续输出]                   │
  │ 段落 5: "好，那我回复他..."  │
  └─────────────────────────────┘
  ↓
[最终回复]
  提取自白结束后的对话内容
```

### 5.3 Prompt 设计

```
# System Prompt

## 你的身份
{角色自我介绍}

## 意识设定
{意识设定内容}

## 记忆能力
你拥有记忆系统，可以在内心对话中使用：
- 「回忆一下」+ 你想回忆的内容 → 系统会帮你检索相关记忆
- 「记住」+ 你要记住的内容 → 系统会帮你写入记忆

## 自白格式
你的输出分为两部分：
1. 自白（内心对话）：用 <monologue>...</monologue> 标签包裹
   - 每一段用换行分隔
   - 需要回忆时，在某一段写「回忆一下」和你想回忆的内容
   - 需要记住时，在某一段写「记住」和你要记住的内容
2. 回复（对外说话）：用 <reply>...</reply> 标签包裹

## 记忆上下文
以下是当前相关的记忆：
{检索到的记忆列表}

## 对话历史
{对话历史}
```

### 5.4 LLM 输出解析

LLM 的输出格式：

```
<monologue>
我在想这件事...
回忆一下上次和绯绯的约定
（记忆系统注入回忆结果）
原来如此，那我明白了。
记住明天要和绯绯见面
（记忆系统注入确认）
好，我准备好了。
</monologue>

<reply>
嗯，我明天确实有事呢...
</reply>
```

### 5.5 流式处理与记忆中断

```python
# chat_engine.py 核心伪代码

async def generate_response(self, user_input: str) -> AsyncIterator[str]:
    # 1. 检索相关记忆作为上下文
    memories = await self.memory_client.nl_query(user_input, top_k=5)

    # 2. 构建 prompt
    prompt = self._build_prompt(user_input, memories)

    # 3. 流式调用 LLM
    buffer = ""
    in_monologue = False
    in_reply = False

    async for chunk in self.llm_client.stream(prompt):
        buffer += chunk

        # 检测标签切换
        if "<monologue>" in buffer:
            in_monologue = True
            buffer = buffer.replace("<monologue>", "")
        if "</monologue>" in buffer:
            in_monologue = False
            buffer = buffer.replace("</monologue>", "")
        if "<reply>" in buffer:
            in_reply = True
            buffer = buffer.replace("<reply>", "")
        if "</reply>" in buffer:
            in_reply = False
            buffer = buffer.replace("</reply>", "")

        # 在自白中检测段落（换行分隔）
        if in_monologue and "\n" in buffer:
            segments = buffer.split("\n")
            for seg in segments[:-1]:  # 最后一段可能不完整
                yield seg  # 显示该段落

                # 检测记忆触发词
                if self._is_recall_trigger(seg):
                    # 暂停输出，调用记忆系统
                    recall_result = await self.memory_client.nl_query(seg, top_k=config.recall_top_k)
                    # 注入记忆结果
                    yield f"（回忆起：{recall_result}）"

                elif self._is_remember_trigger(seg):
                    # 暂停输出，写入记忆
                    write_result = await self.memory_client.nl_query(seg)
                    yield f"（已记住）"

            buffer = segments[-1]  # 保留未完成的段

        # 在回复中直接输出
        elif in_reply:
            yield buffer
            buffer = ""
```

---

## 六、星辰记忆 API 客户端

### 6.1 需要调用的接口

| 用途 | HTTP 方法 | 端点 | 说明 |
|------|----------|------|------|
| NL 查询/写入 | POST | `/api/v1/nl-query` | 自然语言查询，支持写入和查询 |
| MQL 查询 | POST | `/api/v1/query` | 精确查询（可选） |
| 切换系统 | POST | `/api/v1/systems/{name}/use` | 切换活跃记忆系统 |
| 列出系统 | GET | `/api/v1/systems` | 获取可用系统列表 |
| 健康检查 | GET | `/health` | 检查服务器是否在线 |

### 6.2 客户端设计

```python
# memory_client.py

class MemoryClient:
    """星辰记忆 HTTP API 客户端"""

    def __init__(self, base_url: str, api_key: str, system_name: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_name = system_name

    async def health_check(self) -> bool: ...

    async def ensure_system(self) -> None:
        """确保目标记忆系统存在且已激活"""

    async def nl_query(self, text: str, top_k: int = 5) -> NLQueryResult:
        """
        自然语言查询/写入（走 NL Pipeline，自动识别写入/查询意图）

        Returns:
            NLQueryResult:
                - type: "write_only" | "query_only" | "mixed" | "empty"
                - response: NL 生成的回答
                - results: 检索到的记忆列表
                - writes: 写入的记忆数
        """

    async def mql_query(self, mql: str) -> MQLQueryResult:
        """直接执行 MQL 语句（精确查询）"""

@dataclass
class NLQueryResult:
    type: str           # "write_only" | "query_only" | "mixed"
    response: str       # NL 生成的自然语言回答
    results: list[dict] # 检索到的记忆
    writes: int         # 写入的记忆数量

@dataclass
class MQLQueryResult:
    type: str
    data: list[dict]
    affected_rows: int
```

### 6.3 NL Query 的巧妙用法

星辰记忆的 `/api/v1/nl-query` 已经内置意图识别：
- **「回忆一下上次和绯绯的约定」** → 自动识别为查询意图，走 SELECT 路径
- **「记住明天要和绯绯见面」** → 自动识别为写入意图，走 INSERT 路径
- **「我喜欢吃火锅，周末一般干嘛？」** → 混合意图，写入 + 查询

所以 Chat 端**不需要自己拆分意图**，直接把自白段落扔给 NL Query 就行。

---

## 七、LLM 客户端

```python
# llm_client.py

class LLMClient:
    """OpenAI 兼容 LLM 客户端"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 2048, temperature: float = 0.8):
        ...

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式调用 LLM，逐 token 返回"""

    async def complete(self, messages: list[dict]) -> str:
        """非流式调用，返回完整响应"""
```

---

## 八、UI 层

### MVP 版本：终端 UI

```
$ python main.py --character example

🌟 星尘记忆 Chat — 角色: 星织
═══════════════════════════════════════
连接记忆系统: ✅ http://127.0.0.1:8080
LLM 模型: deepseek-v4-flash
═══════════════════════════════════════

你: 明天有什么安排吗？

[星织的内心]
  我在想明天的事...
  回忆一下我明天的安排
  （回忆起：明天下午3点要和绯绯去看电影）
  原来已经约好了。记住要提前出门
  （已记住）
  好，我可以回答了。

星织: 嗯，明天下午和绯绯约了去看电影呢，我得早点出门才行～

你: ...
```

### 后续版本

- Web UI（Gradio / Streamlit）
- Electron 桌面应用

---

## 九、对话引擎核心逻辑

```python
# chat_engine.py

class ChatEngine:
    """对话引擎：管理对话流程、自白过程、记忆集成"""

    def __init__(self, character: CharacterCard, llm: LLMClient,
                 memory: MemoryClient, config: dict):
        self.character = character
        self.llm = llm
        self.memory = memory
        self.config = config
        self.history: list[dict] = []  # 对话历史

    def _build_system_prompt(self, memory_context: str) -> str:
        """构建系统 prompt"""
        ...

    def _build_messages(self, user_input: str, memory_context: str) -> list[dict]:
        """构建完整的 messages 列表"""

    async def _get_memory_context(self, user_input: str) -> str:
        """获取与当前对话相关的记忆上下文"""

    def _is_recall_trigger(self, segment: str) -> bool:
        """检测段落是否包含回忆触发词"""

    def _is_remember_trigger(self, segment: str) -> bool:
        """检测段落是否包含记忆写入触发词"""

    async def chat(self, user_input: str) -> AsyncIterator[ChatEvent]:
        """
        处理一条用户输入，流式返回事件

        ChatEvent 类型:
        - MonologueSegment(text)  — 自白段落
        - MemoryRecall(results)   — 记忆召回结果
        - MemoryWrite(confirmed)  — 记忆写入确认
        - ReplySegment(text)      — 回复片段
        """
```

---

## 十、启动方式

```bash
# 确保星辰记忆服务已启动
cd o:/project/xcmemory_interest
venv/Scripts/python.exe start_server.py --gradio

# 另一个终端启动 Chat
cd o:/project/xcmemory_interest/chat
pip install -r requirements.txt
python main.py --character example
```

### main.py 入口

```python
# main.py
import argparse
import asyncio

def main():
    parser = argparse.ArgumentParser(description="星尘记忆 Chat")
    parser.add_argument("--character", default="example", help="角色卡名称")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 加载角色卡
    character = CharacterCard.load(f"characters/{args.character}.yaml")

    # 初始化客户端
    memory = MemoryClient(
        base_url=config["memory"]["base_url"],
        api_key=config["memory"]["api_key"],
        system_name=config["memory"]["system_name"],
    )
    llm = LLMClient(
        base_url=config["llm"]["base_url"],
        api_key=config["llm"]["api_key"],
        model=config["llm"]["model"],
    )

    # 创建引擎
    engine = ChatEngine(character, llm, memory, config)

    # 启动 UI
    from ui.terminal_ui import TerminalUI
    ui = TerminalUI(engine, character)
    asyncio.run(ui.run())

if __name__ == "__main__":
    main()
```

---

## 十一、开发里程碑

### Phase 1: MVP（最小可用版本）

| # | 任务 | 优先级 |
|---|------|--------|
| 1 | `config.toml` 加载 | P0 |
| 2 | `memory_client.py` — 连接星辰记忆 API | P0 |
| 3 | `llm_client.py` — OpenAI 兼容流式调用 | P0 |
| 4 | `character_card.py` — YAML 角色卡加载 | P0 |
| 5 | `chat_engine.py` — 基本对话（无自白） | P0 |
| 6 | `ui/terminal_ui.py` — 终端交互 | P0 |
| 7 | `main.py` — 启动入口 | P0 |

### Phase 2: 自白 + 记忆集成

| # | 任务 | 优先级 |
|---|------|--------|
| 8 | 自白标签解析（`<monologue>/<reply>`） | P0 |
| 9 | 记忆触发词检测 + 中断处理 | P0 |
| 10 | 记忆结果注入自白 | P0 |
| 11 | 记忆写入确认注入 | P0 |
| 12 | 自白最大段数限制（防死循环） | P1 |

### Phase 3: 体验优化

| # | 任务 | 优先级 |
|---|------|--------|
| 13 | 记忆上下文智能检索（对话前先查相关记忆） | P1 |
| 14 | 对话历史管理（token 限制 + 摘要） | P1 |
| 15 | 多角色卡支持 | P2 |
| 16 | Web UI（Gradio） | P2 |

---

## 十二、关键设计决策（待确认）

### ❓ 1. 自白输出格式

**方案 A**：`<monologue>...</monologue>` + `<reply>...</reply>` 标签
- 优点：解析清晰，LLM 遵循格式能力强
- 缺点：需要 LLM 严格遵循格式

**方案 B**：全部用自然语言，靠换行分段，第一段起至某标记为自白
- 优点：更自然
- 缺点：解析不稳定

**建议**：方案 A，标签格式更可靠。

### ❓ 2. 记忆上下文注入时机

**方案 A**：每次对话前，用用户输入检索一次记忆，注入 system prompt
- 优点：简单可靠
- 缺点：只能基于用户输入检索，角色自白中产生的查询无法预知

**方案 B**：仅在自白触发回忆时才查询，不在对话前预检索
- 优点：更"真实"——角色主动回忆
- 缺点：可能遗漏明显相关的记忆

**方案 C**：A+B 结合——对话前轻度检索（top 3），自白中深度检索（top 5）
- 优点：兼顾两者
- 缺点：API 调用更多

**建议**：方案 C。

### ❓ 3. 自白中记忆触发的粒度

**方案 A**：整段作为 NL 查询提交
- 优点：简单
- 缺点：段落中可能有非记忆内容，噪音大

**方案 B**：提取触发词后的内容作为查询
- 优点：查询更精确
- 缺点：需要额外解析

**建议**：方案 B，提取「回忆一下」后面的内容作为查询文本。

### ❓ 4. holder 映射

角色的 `name` 自动映射为星辰记忆的 `holder`，这样 NL 查询中的"我"就会被正确映射。

---

## 十三、依赖

```
# requirements.txt
openai>=1.0.0
httpx>=0.25.0
pyyaml>=6.0
tomli>=2.0;python_version<"3.11"
rich>=13.0        # 终端美化
```

不依赖 torch、chromadb 等重依赖——Chat 通过 HTTP API 调用星辰记忆服务，不需要直接访问向量数据库。
