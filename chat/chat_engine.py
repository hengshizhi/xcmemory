# -*- coding: utf-8 -*-
"""
对话引擎

架构：
1. 记忆管家：对话上下文 + 已知记忆 → 生成查询 → 检索 → 加工转述
2. 扮演 LLM：角色卡 + 记忆转述 + 最近 4 轮对话 + 本次用户输入 → 流式回复
3. 记忆写入：对话后提取实证信息 → 逐条保存
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

from character_card import CharacterCard
from llm_client import LLMClient
from memory_client import MemoryClient


# ============================================================================
# 事件类型
# ============================================================================

class EventType(str, Enum):
    MEMORY_QUERY = "memory_query"
    MEMORY_RESULT = "memory_result"
    MEMORY_SAVE = "memory_save"
    REPLY_SEGMENT = "reply_segment"
    REPLY_END = "reply_end"
    ERROR = "error"


@dataclass
class ChatEvent:
    type: EventType
    text: str = ""
    data: Optional[dict] = None


# ============================================================================
# 记忆管家 Prompts
# ============================================================================

QUERY_GEN_PROMPT = """根据对话上下文和已知记忆，从用户最新消息中提取需要检索的疑问，生成查询。每个查询必须以问号或疑问词结尾。
不需要检索时输出「无」。

对话上下文：
{context}

已知记忆：
{known}

用户最新消息：{query}

查询词："""


PARAPHRASE_PROMPT = """根据已知信息和用户的问题，用第二人称「你」生成角色已知事实的转述。
如果没有任何已知信息，输出「无」。

用户问题：{query}

已知信息：
{known}

转述（第二人称）："""


# ============================================================================
# 记忆写入 Prompt
# ============================================================================

EXTRACT_FACTS_PROMPT = """从对话中提取具有长期价值的实证信息，用第一人称「我」的视角，以一段连贯的话表述，不要分点列条。
注意人称转换：对方说的「你」→「我」，对方说的「我」→角色名「绯绯」。
包括：身份、关系、性格、喜好、约定、事件、世界知识。
排除以下短暂无价值信息：表情动作（笑、愣、抱、点头等）、语气描写、临时情绪、对话流程描述。
没有可保存的信息时输出「无」。

对话：
{conversation}

陈述："""


# ============================================================================
# 扮演 LLM 系统提示
# ============================================================================

ROLEPLAY_SYSTEM_TEMPLATE = """你是{name}。你正在与{user_name}对话。
{card}

已知信息：
{memory}

【最高指令】你的任何回答必须基于以上「已知信息」。如果已知信息中不包含回答所需的内容，你只能回答「我不记得」「我不确定」「我不知道」，严禁编造任何事实。"""


# ============================================================================
# 对话引擎
# ============================================================================

class ChatEngine:
    def __init__(
        self,
        character: CharacterCard,
        llm: LLMClient,
        memory: MemoryClient,
        config: dict,
        user_name: str = "你",
    ):
        self.character = character
        self.llm = llm
        self.memory = memory
        self.config = config
        self.user_name = user_name
        self.history: list[dict] = []
        self.known_memories: list[str] = []

        mono_cfg = config.get("monologue", {})
        self.recall_top_k: int = mono_cfg.get("recall_top_k", 5)

    # ── 记忆管家 ───────────────────────────────────────────

    async def _memory_manager(self, user_input: str) -> AsyncIterator[ChatEvent]:
        yield ChatEvent(type=EventType.MEMORY_QUERY, text="🔍 记忆管家分析中...")

        # 上下文与扮演 LLM 同步（最近 4 轮）
        ctx = []
        for msg in self.history[-8:]:
            role = self.character.name if msg["role"] == "assistant" else self.user_name
            ctx.append(f"{role}: {msg['content'][:200]}")
        context = "\n".join(ctx) if ctx else "（无）"
        known_text = "\n".join(f"- {k}" for k in self.known_memories) if self.known_memories else "（无）"

        # Step 1: 生成查询
        queries_text = await self.llm.complete([
            {"role": "user", "content": QUERY_GEN_PROMPT.format(
                context=context, known=known_text, query=user_input)},
        ])
        queries_text = queries_text.strip()
        if "查询词：" in queries_text:
            queries_text = queries_text.split("查询词：", 1)[1]
        queries_text = queries_text.strip()
        queries = [q.strip() for q in queries_text.split("\n") if q.strip() and q.strip() != "无"]
        yield ChatEvent(type=EventType.MEMORY_QUERY, text=f"查询: {' '.join(queries) if queries else '(空)'}")

        # Step 2: 执行查询
        new_items = []
        if queries:
            combined = " ".join(queries)
            result = await self.memory.nl_query(combined, top_k=self.recall_top_k)
            if result.type != "error" and result.results:
                for mem in result.results[:10]:
                    c = mem.get("content", "") or mem.get("query_sentence", "")
                    if c and c not in self.known_memories and c not in new_items:
                        new_items.append(c)

        if new_items:
            self.known_memories.extend(new_items)
            if len(self.known_memories) > 30:
                self.known_memories = self.known_memories[-30:]

        # Step 3: 生成转述
        known_text = "\n".join(f"- {k}" for k in self.known_memories) if self.known_memories else "（无）"
        paraphrase = await self.llm.complete([
            {"role": "user", "content": PARAPHRASE_PROMPT.format(
                query=user_input, known=known_text)},
        ])
        paraphrase = paraphrase.strip()
        if paraphrase and paraphrase != "无":
            yield ChatEvent(type=EventType.MEMORY_RESULT, text=paraphrase)

    # ── 构建扮演 messages ──────────────────────────────────

    def _build_messages(self, user_input: str, memory_context: str) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": ROLEPLAY_SYSTEM_TEMPLATE.format(
                    name=self.character.name,
                    user_name=self.user_name,
                    card=self.character.get_system_prompt_section(),
                    memory=memory_context or "无",
                ),
            },
        ]
        messages.extend(self.history[-8:])
        messages.append({"role": "user", "content": user_input})
        return messages

    # ── 对话入口 ────────────────────────────────────────────

    async def chat(self, user_input: str) -> AsyncIterator[ChatEvent]:
        # 1. 记忆管家
        memory_text = ""
        try:
            async for event in self._memory_manager(user_input):
                if event.type == EventType.MEMORY_RESULT:
                    memory_text = event.text
                yield event
        except Exception as e:
            yield ChatEvent(type=EventType.ERROR, text=f"记忆管家错误: {e}")

        # 2. 扮演 LLM
        messages = self._build_messages(user_input, memory_text)
        try:
            full_reply = ""
            async for token in self.llm.stream(messages):
                full_reply += token
                yield ChatEvent(type=EventType.REPLY_SEGMENT, text=token)
        except Exception as e:
            yield ChatEvent(type=EventType.ERROR, text=f"LLM 错误: {e}")
        yield ChatEvent(type=EventType.REPLY_END)

        # 3. 记忆写入
        reply_text = full_reply.strip()
        conversation = f"{self.user_name}: {user_input}\n{self.character.name}: {reply_text}"
        facts_text = await self.llm.complete([
            {"role": "user", "content": EXTRACT_FACTS_PROMPT.format(conversation=conversation)},
        ])
        facts = [f.strip() for f in facts_text.split("\n") if f.strip() and f.strip() != "无"]
        if facts:
            saved = 0
            for fact in facts:
                result = await self.memory.nl_query(f"记住: {fact}", top_k=1)
                if result.type == "write_only":
                    saved += 1
            preview = "；".join(facts[:3])
            if len(facts) > 3:
                preview += "…"
            yield ChatEvent(
                type=EventType.MEMORY_SAVE,
                text=f"已写入 {saved}/{len(facts)} 条 ({preview})",
            )
        else:
            yield ChatEvent(type=EventType.MEMORY_SAVE, text="未提取到可保存的记忆")

        self._last_reply = reply_text

    # ── 历史 ────────────────────────────────────────────────

    def save_to_history(self, user_input: str):
        self.history.append({"role": "user", "content": user_input})
        if hasattr(self, "_last_reply") and self._last_reply:
            self.history.append({"role": "assistant", "content": self._last_reply})
        if len(self.history) > 40:
            self.history = self.history[-40:]

    def clear_history(self):
        self.history = []
        self.known_memories = []
