# -*- coding: utf-8 -*-
"""
OpenAI 兼容 LLM 客户端

支持 OpenAI / DeepSeek / OpenRouter 等兼容 API 的流式调用。
"""

from typing import AsyncIterator

import httpx
from openai import AsyncOpenAI


class LLMClient:
    """OpenAI 兼容 LLM 客户端"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.8,
        timeout: float = 120.0,
    ):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(timeout=timeout),
        )
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """
        流式调用 LLM，逐 token 返回。

        Args:
            messages: OpenAI 格式的消息列表

        Yields:
            每个 token 字符串
        """
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def complete(self, messages: list[dict]) -> str:
        """
        非流式调用，返回完整响应。

        Args:
            messages: OpenAI 格式的消息列表

        Returns:
            完整的响应文本
        """
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=False,
        )
        return resp.choices[0].message.content if resp.choices else ""
