# -*- coding: utf-8 -*-
"""
对话引擎 — 核心模块

管理对话流程、自白过程、记忆系统集成。

核心流程：
1. 用户输入 → 轻度记忆检索（prefetch）
2. 构建 Prompt（角色卡 + 记忆上下文 + 对话历史 + 自白指令）
3. LLM 流式生成自白 + 回复
4. 自白按换行分段，逐段检测记忆触发词
5. 触发时暂停输出，调用记忆系统，结果注入自白
6. 继续输出直到自白结束
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

from character_card import CharacterCard
from llm_client import LLMClient
from memory_client import MemoryClient, NLQueryResult


# ============================================================================
# 事件类型
# ============================================================================

class EventType(str, Enum):
    """流式输出事件类型"""
    MONOLOGUE_START = "monologue_start"     # 自白开始
    MONOLOGUE_SEGMENT = "monologue_segment"  # 自白段落
    MEMORY_RECALL = "memory_recall"          # 记忆召回结果
    MEMORY_WRITE = "memory_write"            # 记忆写入确认
    MONOLOGUE_END = "monologue_end"          # 自白结束
    REPLY_SEGMENT = "reply_segment"          # 回复片段
    REPLY_END = "reply_end"                  # 回复结束
    ERROR = "error"                          # 错误


@dataclass
class ChatEvent:
    """对话事件"""
    type: EventType
    text: str = ""
    data: Optional[dict] = None


# ============================================================================
# System Prompt 模板
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """\
{character_section}

## ⛔ 必须先自白再回复（最高优先级）
你的每次回复都**必须**以 <monologue>...</monologue> 开头，然后才能输出 <reply>...</reply>。

**为什么自白必不可少？**
自白是你**处理记忆的唯一途径**。你在自白中用「记住」写入记忆、用「回忆一下」检索记忆。如果跳过自白直接回复，系统**
没有机会**帮你写入或回忆任何信息——对方告诉你的事情会被永远遗忘。

❌ 错误（直接回复，没有自白会导致记忆丢失）：
<reply>嗯，记住了。原来我是18岁的女性。</reply>

✅ 正确（先自白处理记忆，再回复）：
<monologue>
记住我是18岁的女性
对方告诉我这是我的基本信息，先记下来。
</monologue>
嗯，记住了。原来我是18岁的女性。

即使回复只有两个字（"好的""嗯"），也要先写自白。不写自白 = 放弃记忆。

## 记忆能力
你拥有记忆系统，可以在自白中使用：
- 「回忆一下」+ 你想回忆的内容 → 系统会帮你检索相关记忆
- 「记住」+ 你要记住的内容 → 系统会帮你写入记忆

## 输出格式（严格遵守）
你的输出分为两部分：
1. 自白：用 <monologue>...</monologue> 标签包裹，是内心思考过程
   - 每一段用换行分隔
   - 需要记忆操作时，在段落中写「回忆一下」或「记住」
2. 回复：**自白之后的所有文本**就是你对用户说的话
   - **不需要任何标签包裹**，直接写自然对话
   - 不要在回复中用「记住」「回忆一下」

示例：
<monologue>
记住绯绯喜欢咖啡
他提过好几次，看来是真喜欢。
</monologue>
嗯，我记得，你确实很喜欢咖啡。明天要不要一起去那家新开的店？

## ⚠️ 硬性规则：记忆操作只能在自白中使用
「记住」和「回忆一下」只能出现在 <monologue>...</monologue> 内部。
自白外的回复文本中严禁出现这些指令。

❌ 错误：自白外用「记住绯绯喜欢咖啡」我们明天见。
✅ 正确：
<monologue>
记住绯绯喜欢咖啡
</monologue>
嗯，我记住了。我们明天见。

## 记忆上下文
以下是当前相关的记忆：
{memory_context}
"""

# ============================================================================
# 引导模式 Prompt 模板
# ============================================================================

ONBOARDING_SYSTEM_PROMPT_TEMPLATE = """\
{character_section}

## ⛔ 必须先自白再回复（最高优先级）
你的每次回复都**必须**以 <monologue>...</monologue> 开头，然后才能输出 <reply>...</reply>。

**为什么自白必不可少？**
自白是你**建立记忆的唯一途径**。你现在没有任何记忆，需要通过自白中的「记住」把对方告诉你的信息写入记忆系统。
如果跳过自白直接回复，系统**没有机会**帮你写入信息——对方刚刚告诉你的关于你的一切会被永远遗忘。
**不写自白 = 放弃记忆 = 你永远不知道自己是谁。**

❌ 错误（直接回复会导致信息丢失）：
<reply>嗯，记住了。原来我是18岁的女性。</reply>

✅ 正确（先自白记住信息，再回应对方）：
<monologue>
记住我是18岁的女性
记住我的性格是理论探索者和旅行者思维
对方在帮我建立自我认知，每一条都要认真记下来。
</monologue>
嗯，记住了。原来我是18岁的女性。

即使回复只有两个字，也要先写自白。**不写自白 = 放弃记忆。**

## 当前状态
你刚刚诞生，还没有任何记忆。现在正与 {user_name} 进行第一次对话。
你需要通过与 {user_name} 的交流，逐步了解自己是谁、有什么经历、喜欢什么、不喜欢什么。

## 记忆能力
你拥有记忆系统，可以在自白中使用：
- 「记住」+ 你要记住的内容 → 系统会帮你写入记忆

在引导阶段，务必在自白中**主动用「记住」**写入重要信息。
例如，当 {user_name} 告诉你一些关于你的事情时，在自白中写：
「记住我{{一些关于自己的信息}}」

## 输出格式（严格遵守）
你的输出分为两部分：
1. 自白：用 <monologue>...</monologue> 标签包裹，是内心思考过程
   - 每一段用换行分隔
   - 获得新认知时用「记住」写入记忆
2. 回复：**自白之后的所有文本**就是你对用户说的话
   - **不需要任何标签包裹**，直接写自然对话
   - 不要在回复中用「记住」（那是自白中的指令）

## ⚠️ 硬性规则：记忆操作只能在自白中使用
「记住」只能出现在 <monologue>...</monologue> 内部。
自白外的回复文本中严禁出现「记住」。

❌ 错误：自白外用「记住我是星织，有个哥哥叫绯绯」
✅ 正确：
<monologue>记住我是星织，有个哥哥叫绯绯</monologue>
嗯，我记住了。原来我有个哥哥叫绯绯。

## 引导目标
通过对话逐步了解自己，包括但不限于：
- 你的身份和背景
- 你的性格特点和喜好
- 你过去的经历
- 你与他人的关系
- 你的知识和技能

每次获得新的自我认知时，在自白中用「记住」写入记忆系统。而回复中只需要自然对话。
"""


# ============================================================================
# 对话引擎
# ============================================================================

class ChatEngine:
    """对话引擎：管理对话流程、自白过程、记忆集成"""

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

        # 自白配置
        mono_cfg = config.get("monologue", {})
        self.recall_triggers: list[str] = mono_cfg.get("recall_triggers", [
            "回忆一下", "回忆", "记得", "记忆中", "之前",
        ])
        self.remember_triggers: list[str] = mono_cfg.get("remember_triggers", [
            "记住", "记住这个", "铭记", "记下来", "要记得",
        ])
        self.max_segments: int = mono_cfg.get("max_segments", 20)
        self.recall_top_k: int = mono_cfg.get("recall_top_k", 5)
        self.prefetch_top_k: int = mono_cfg.get("prefetch_top_k", 3)

    # ── Prompt 构建 ─────────────────────────────────────────

    def _build_system_prompt(self, memory_context: str) -> str:
        """构建系统 prompt"""
        return SYSTEM_PROMPT_TEMPLATE.format(
            character_section=self.character.get_system_prompt_section(),
            memory_context=memory_context,
        )

    def _build_onboarding_system_prompt(self) -> str:
        """构建引导模式的系统 prompt"""
        return ONBOARDING_SYSTEM_PROMPT_TEMPLATE.format(
            character_section=self.character.get_system_prompt_section(),
            user_name=self.user_name,
        )

    def _build_messages(self, user_input: str, memory_context: str) -> list[dict]:
        """构建完整的 messages 列表"""
        messages = [
            {"role": "system", "content": self._build_system_prompt(memory_context)},
        ]
        # 加入对话历史
        messages.extend(self.history)
        # 加入当前用户输入
        messages.append({"role": "user", "content": user_input})
        return messages

    def _build_onboarding_messages(self, user_input: str) -> list[dict]:
        """构建引导模式的 messages 列表"""
        messages = [
            {"role": "system", "content": self._build_onboarding_system_prompt()},
        ]
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_input})
        return messages

    # ── 记忆上下文 ──────────────────────────────────────────

    async def _get_memory_context(self, user_input: str) -> str:
        """
        获取与当前对话相关的记忆上下文（对话前轻度检索）。
        """
        result = await self.memory.nl_query(user_input, top_k=self.prefetch_top_k)

        if result.type == "error":
            return "（记忆系统暂时不可用）"

        if not result.results:
            return "（暂无相关记忆）"

        lines = []
        for i, mem in enumerate(result.results[:self.prefetch_top_k], 1):
            content = mem.get("content", "") or mem.get("query_sentence", "")
            if content:
                lines.append(f"{i}. {content}")

        return "\n".join(lines) if lines else "（暂无相关记忆）"

    # ── 触发词检测 ──────────────────────────────────────────

    def _is_recall_trigger(self, segment: str) -> bool:
        """检测段落是否包含回忆触发词"""
        return any(t in segment for t in self.recall_triggers)

    def _is_remember_trigger(self, segment: str) -> bool:
        """检测段落是否包含记忆写入触发词"""
        return any(t in segment for t in self.remember_triggers)

    def _is_meaningful_write(self, extracted_text: str) -> bool:
        """过滤假阳性：提取出的文本必须有实质内容，不能只是「了」「啦」等语气词"""
        stripped = extracted_text.strip()
        # 去掉「记住:」前缀后判断
        if stripped.startswith("记住:") or stripped.startswith("记住："):
            stripped = stripped[3:].strip()
        if len(stripped) < 2:
            return False
        # 纯语气词/标点
        particles = {"了", "啦", "哦", "啊", "嗯", "呢", "吧", "吗", "呀", "嘛", "哈"}
        if stripped in particles:
            return False
        return True

    def _extract_query_text(
        self, segment: str, triggers: list[str], keep_trigger_prefix: bool = False
    ) -> str:
        """
        提取触发词后面的内容作为查询文本。

        例如：「回忆一下上次和绯绯的约定」→「上次和绯绯的约定」
        如果 keep_trigger_prefix=True →「回忆一下: 上次和绯绯的约定」
        """
        for trigger in sorted(triggers, key=len, reverse=True):
            if trigger in segment:
                idx = segment.index(trigger) + len(trigger)
                query = segment[idx:].strip()
                # 去掉首尾标点/括号/残留标签字符
                query = re.sub(r"^[，。、：:！!？?\s「」『』""''<>/]+", "", query)
                query = re.sub(r"[，。、：:！!？?\s「」『』""''<>/]+$", "", query)
                if query and keep_trigger_prefix:
                    return f"{trigger}: {query}"
                return query if query else segment
        return segment

    # ── 引导模式 ────────────────────────────────────────────

    async def onboarding_chat(self, user_input: str) -> AsyncIterator[ChatEvent]:
        """
        引导模式对话：角色通过与用户交流，逐步了解自己并写入记忆。

        流程与普通对话相同，但使用引导模式的 system prompt，
        且不检索记忆上下文（因为还没有记忆）。
        """
        messages = self._build_onboarding_messages(user_input)
        async for event in self._stream_and_process(messages):
            yield event

    # ── 核心对话流程 ────────────────────────────────────────

    async def chat(self, user_input: str) -> AsyncIterator[ChatEvent]:
        """
        处理一条用户输入，流式返回事件。

        流程：
        1. 预检索记忆上下文
        2. 构建 prompt
        3. 流式生成自白 + 回复
        4. 自白中检测触发词，触发记忆操作
        """
        # 1. 预检索记忆上下文
        memory_context = await self._get_memory_context(user_input)

        # 2. 构建 messages
        messages = self._build_messages(user_input, memory_context)

        # 3. 流式处理
        async for event in self._stream_and_process(messages):
            yield event

    async def _stream_and_process(
        self, messages: list[dict]
    ) -> AsyncIterator[ChatEvent]:
        """流式处理 LLM 输出，含自白分段和记忆触发"""
        buffer = ""
        in_monologue = False
        monologue_started = False
        monologue_ended = False       # </monologue> 已闭合，之后都是回复
        segment_count = 0
        full_reply = ""

        try:
            async for token in self.llm.stream(messages):
                buffer += token

                # ── 自白开始 ──
                if not monologue_ended and "<monologue>" in buffer:
                    in_monologue = True
                    monologue_started = True
                    buffer = buffer.replace("<monologue>", "")
                    yield ChatEvent(type=EventType.MONOLOGUE_START)

                # ── 自白结束 ──
                if in_monologue and "</monologue>" in buffer:
                    text_before = buffer.replace("</monologue>", "")
                    if text_before.strip():
                        async for evt in self._process_monologue_segment(
                            text_before.strip(), segment_count
                        ):
                            yield evt
                            segment_count += 1
                    in_monologue = False
                    monologue_ended = True
                    buffer = ""
                    yield ChatEvent(type=EventType.MONOLOGUE_END)
                    continue

                # ── 向后兼容：仍识别 <reply> 标签 ──
                if "<reply>" in buffer:
                    buffer = buffer.replace("<reply>", "")
                if "</reply>" in buffer:
                    text_before = buffer.replace("</reply>", "")
                    if text_before:
                        full_reply += text_before
                        yield ChatEvent(type=EventType.REPLY_SEGMENT, text=text_before)
                    buffer = ""
                    yield ChatEvent(type=EventType.REPLY_END)
                    continue

                # ── 自白段落处理（按换行分段）──
                if in_monologue and "\n" in buffer:
                    segments = buffer.split("\n")
                    # 最后一段可能不完整，保留
                    for seg in segments[:-1]:
                        seg = seg.strip()
                        if not seg:
                            continue

                        async for evt in self._process_monologue_segment(
                            seg, segment_count
                        ):
                            yield evt
                        segment_count += 1

                        # 防死循环
                        if segment_count >= self.max_segments:
                            yield ChatEvent(
                                type=EventType.ERROR,
                                text="⚠️ 自白段数超过上限，强制结束",
                            )
                            break

                    buffer = segments[-1]

                # ── 回复直接输出（</monologue> 之后的所有文本）──
                elif monologue_ended and not in_monologue:
                    full_reply += buffer
                    yield ChatEvent(type=EventType.REPLY_SEGMENT, text=buffer)
                    buffer = ""

        except Exception as e:
            yield ChatEvent(type=EventType.ERROR, text=f"LLM 调用错误: {e}")

        # ── 流结束后：输出 buffer 残留的回复文本 ──
        if monologue_ended and buffer.strip():
            full_reply += buffer.strip()
            yield ChatEvent(type=EventType.REPLY_SEGMENT, text=buffer.strip())

        # 如果完全没有任何输出（LLM 返回空）
        if not monologue_started and not full_reply.strip():
            yield ChatEvent(type=EventType.ERROR, text="LLM 未返回内容，请重试")

        # 结束回复
        if monologue_started:
            yield ChatEvent(type=EventType.REPLY_END)

        # 如果 LLM 没有输出标签格式（降级处理）
        if not monologue_started and buffer.strip():
            full_reply = buffer.strip()
            yield ChatEvent(type=EventType.REPLY_SEGMENT, text=full_reply)
            yield ChatEvent(type=EventType.REPLY_END)

        # ── 回复兜底扫描：检测是否误将「记住」写入了 reply ──
        if full_reply.strip():
            for line in full_reply.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if self._is_remember_trigger(line):
                    write_text = self._extract_query_text(
                        line, self.remember_triggers, keep_trigger_prefix=True
                    )
                    if not self._is_meaningful_write(write_text):
                        continue
                    yield ChatEvent(
                        type=EventType.MEMORY_WRITE,
                        text=f"📝 记住（回复兜底）: {write_text}",
                    )
                    result = await self.memory.nl_query(write_text, top_k=1)
                    if result.type == "error":
                        confirm = f"（记忆写入失败: {result.response}）"
                    elif result.writes > 0:
                        confirm = "（已记住）"
                    else:
                        confirm = f"（记忆未写入: API 返回 type={result.type}）"
                    yield ChatEvent(
                        type=EventType.MONOLOGUE_SEGMENT,
                        text=confirm,
                    )

        # 保存对话历史
        # 注意：在 _stream_and_process 中无法获取 user_input，
        # 调用方需要自行调用 save_to_history
        if full_reply.strip():
            self._last_reply = full_reply.strip()
        else:
            self._last_reply = ""

    def save_to_history(self, user_input: str):
        """保存一轮对话到历史"""
        self.history.append({"role": "user", "content": user_input})
        if hasattr(self, "_last_reply") and self._last_reply:
            self.history.append({"role": "assistant", "content": self._last_reply})

        # 控制历史长度（保留最近 20 轮）
        max_history = 40
        if len(self.history) > max_history:
            self.history = self.history[-max_history:]

    async def _process_monologue_segment(
        self, segment: str, segment_idx: int
    ) -> AsyncIterator[ChatEvent]:
        """
        处理自白中的一个段落：输出文本 + 检测触发词 + 记忆操作。
        """
        # 先输出段落文本
        yield ChatEvent(type=EventType.MONOLOGUE_SEGMENT, text=segment)

        # 检测回忆触发
        if self._is_recall_trigger(segment):
            query_text = self._extract_query_text(segment, self.recall_triggers)
            yield ChatEvent(
                type=EventType.MEMORY_RECALL,
                text=f"🔍 回忆: {query_text}",
            )

            result = await self.memory.nl_query(query_text, top_k=self.recall_top_k)

            if result.type == "error":
                recall_text = f"（回忆失败: {result.response}）"
            elif result.results:
                # 提取记忆内容
                memory_lines = []
                for i, mem in enumerate(result.results[:self.recall_top_k], 1):
                    content = mem.get("content", "") or mem.get("query_sentence", "")
                    if content:
                        memory_lines.append(content)
                recall_text = f"（回忆起：{'；'.join(memory_lines)}）"
            else:
                recall_text = "（没有找到相关记忆）"

            yield ChatEvent(
                type=EventType.MONOLOGUE_SEGMENT,
                text=recall_text,
            )

        # 检测记忆写入触发
        elif self._is_remember_trigger(segment):
            write_text = self._extract_query_text(
                segment, self.remember_triggers, keep_trigger_prefix=True
            )
            if not self._is_meaningful_write(write_text):
                # 假阳性（如"记住了。"）→ 不触发
                yield ChatEvent(type=EventType.MONOLOGUE_SEGMENT, text=segment)
                return
            yield ChatEvent(
                type=EventType.MEMORY_WRITE,
                text=f"📝 记住: {write_text}",
            )

            result = await self.memory.nl_query(write_text, top_k=1)

            if result.type == "error":
                confirm_text = f"（记忆写入失败: {result.response}）"
            elif result.writes > 0:
                confirm_text = "（已记住）"
            else:
                confirm_text = f"（记忆未写入: API 返回 type={result.type}）"

            yield ChatEvent(
                type=EventType.MONOLOGUE_SEGMENT,
                text=confirm_text,
            )

    # ── 辅助方法 ────────────────────────────────────────────

    def clear_history(self):
        """清除对话历史"""
        self.history = []

    def get_history_length(self) -> int:
        """获取对话历史轮数"""
        return len(self.history) // 2
