# -*- coding: utf-8 -*-
"""
检索充分性检查 (Sufficiency Checker)

判断已检索的记忆内容是否足够回答用户查询。
如果不够充分，可能触发扩展检索。

参考：MEMU_TEXT2MEM_REFERENCE.md 第四章
"""

from __future__ import annotations

import re

from openai import AsyncClient


# =============================================================================
# Prompt 模板
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
# SufficiencyChecker
# =============================================================================


class SufficiencyChecker:
    """
    判断检索结果是否充分，可能触发扩展检索。

    Attributes:
        llm: OpenAI API 客户端（AsyncClient 实例）

    Example:
        client = AsyncClient(api_key="sk-...")
        checker = SufficiencyChecker(client)
        is_enough, reason = await checker.check(
            query="我之前学 Python 时遇到什么问题来着",
            retrieved_content="用户在2024年3月开始学习Python，使用PyCharm IDE..."
        )
    """

    def __init__(self, llm_client: AsyncClient, model: str = "gpt-4o-mini"):
        """
        初始化充分性检查器。

        Args:
            llm_client: OpenAI AsyncClient 实例，与 mql_generator.py 中的接口保持一致
            model: LLM 模型名称，默认 "gpt-4o-mini"
        """
        self.llm = llm_client
        self.model = model

    async def check(self, query: str, retrieved_content: str) -> tuple[bool, str]:
        """
        判断检索内容是否足够回答用户查询。

        Args:
            query: 用户查询
            retrieved_content: 已检索到的记忆内容

        Returns:
            tuple[bool, str]: (是否足够, 判断理由)
                - is_enough: True 表示 ENOUGH，False 表示 MORE
                - reason: 判断理由
        """
        prompt = SUFFICIENCY_PROMPT.format(
            query=query,
            content=retrieved_content
        )

        response = await self.llm.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=300,
        )

        response_text = response.choices[0].message.content
        judgement = self._extract_tag(response_text, "judgement")
        consideration = self._extract_tag(response_text, "consideration")

        return (judgement == "ENOUGH", consideration)

    def _extract_tag(self, text: str, tag: str) -> str:
        """
        从文本中提取指定 XML 标签内容。

        Args:
            text: 原始响应文本
            tag: 标签名（不含 <>）

        Returns:
            标签内容，如果未找到则返回空字符串
        """
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""


# =============================================================================
# LLM Client 示例
# =============================================================================


class LLMClient:
    """
    OpenAI API 异步客户端封装示例。

    Usage:
        client = LLMClient(api_key="sk-...", model="gpt-4o")
        checker = SufficiencyChecker(client)
        is_enough, reason = await checker.check("用户问题", "检索到的内容")
    """

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        """
        初始化 LLM 客户端。

        Args:
            api_key: OpenAI API 密钥
            model: 模型名称，默认 gpt-4o
        """
        self.model = model
        self._api_key = api_key

    async def chat(self, prompt: str, system: str = None) -> str:
        """
        发送聊天请求（兼容 mql_generator.py 的接口）。

        Args:
            prompt: 用户 prompt
            system: 系统 prompt（可选）

        Returns:
            模型生成的文本内容
        """
        client = AsyncClient(api_key=self._api_key)
        messages = []

        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.0,
        )
        return response.choices[0].message.content
