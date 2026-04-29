# -*- coding: utf-8 -*-
"""
OpenAI 兼容 LLM 客户端
"""

from typing import AsyncIterator, Optional

import httpx
from openai import AsyncOpenAI


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 100000,
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

    async def complete(self, messages: list[dict]) -> str:
        """非流式调用。"""
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=False,
        )
        return resp.choices[0].message.content if resp.choices else ""

    async def stream_with_thinking(self, messages: list[dict]) -> AsyncIterator[tuple[str, str]]:
        """流式 + 思考模式。yields ("think", token) 或 ("reply", token)。"""
        s = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
            extra_body={"thinking": {"type": "enabled"}},
        )
        async for chunk in s:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.reasoning_content:
                yield ("think", delta.reasoning_content)
            if delta.content:
                yield ("reply", delta.content)

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式调用。"""
        s = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )
        async for chunk in s:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
