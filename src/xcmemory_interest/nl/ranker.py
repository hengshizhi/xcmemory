# -*- coding: utf-8 -*-
"""
LLM 重排序 (LLM Ranker)

在向量初筛结果上，用 LLM 做二次精排，只返回真正与查询相关的记忆项。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..prompts.nl import RANKER_PROMPT


# =============================================================================
# MemoryItemRanker
# =============================================================================

from ..prompts.nl import RANKER_PROMPT

# =============================================================================
# MemoryItemRanker
# =============================================================================


class MemoryItemRanker:
    """
    用 LLM 对召回的记忆项进行重排序。

    在向量初筛结果上，用 LLM 做二次精排，只返回真正与查询相关的记忆项，
    并按相关性排序。

    Attributes:
        llm: LLM 客户端，需提供 async def chat(prompt: str) -> str 方法
    """

    def __init__(self, llm_client: Any, model: str = "gpt-4o-mini"):
        """
        初始化 LLM 重排序器。

        Args:
            llm_client: LLM 客户端，需提供 async chat.completions.create() 方法
            model: LLM 模型名称，默认 "gpt-4o-mini"
        """
        self.llm = llm_client
        self.model = model

    async def _call_llm(self, prompt: str) -> str:
        """
        调用 LLM 接口。

        Args:
            prompt: 完整的 prompt 字符串

        Returns:
            LLM 生成的原始响应文本
        """
        response = await self.llm.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        return response.choices[0].message.content or ""

    async def rank(self, query: str, items: list[dict[str, Any]], top_k: int = 5) -> list[str]:
        """
        对记忆项进行 LLM 重排序，返回排序后的 item_id 列表。

        Args:
            query: 用户查询字符串
            items: 记忆项列表，每个 dict 包含：
                   - id: 记忆 ID
                   - query_sentence: 六槽查询句
                   - content: 记忆内容
                   - lifecycle: 生命周期（秒）
            top_k: 最多返回的结果数，默认 5

        Returns:
            排序后的 item_id 列表，第一个是最相关的项
        """
        if not items:
            return []

        if top_k <= 0:
            return []

        # 格式化 items 数据
        items_data = self._format_items(items)

        # 构建 prompt
        prompt = RANKER_PROMPT.format(
            query=query,
            items_data=items_data,
            top_k=top_k,
        )

        # 调用 LLM
        response = await self._call_llm(prompt)

        # 解析响应
        result = self._parse_response(response)
        item_ids = result.get("items", [])

        # 截取 top_k
        return item_ids[:top_k]

    def _format_items(self, items: list[dict[str, Any]]) -> str:
        """
        将记忆项列表格式化为字符串，供 LLM 审阅。

        Args:
            items: 记忆项列表

        Returns:
            格式化后的字符串
        """
        formatted_items = []
        for item in items:
            item_id = item.get("id", "unknown")
            query_sentence = item.get("query_sentence", "")
            content = item.get("content", "")
            lifecycle = item.get("lifecycle", 0)

            # 格式化 lifecycle 为可读时间
            lifecycle_str = self._format_lifecycle(lifecycle)

            formatted_items.append(
                f'{{'
                f'"id": "{item_id}", '
                f'"query_sentence": "{self._escape_json(query_sentence)}", '
                f'"content": "{self._escape_json(content)}", '
                f'"lifecycle": {lifecycle}, '
                f'"lifecycle_desc": "{lifecycle_str}"'
                f'}}'
            )

        return "[\n" + ",\n".join(formatted_items) + "\n]"

    def _format_lifecycle(self, lifecycle: int) -> str:
        """
        将 lifecycle 秒数转换为可读字符串。

        Args:
            lifecycle: 生命周期秒数

        Returns:
            可读的时间描述
        """
        if lifecycle >= 999999:
            return "永久"
        elif lifecycle >= 86400:
            days = lifecycle // 86400
            return f"{days}天" if days > 1 else "1天"
        elif lifecycle >= 3600:
            hours = lifecycle // 3600
            return f"{hours}小时" if hours > 1 else "1小时"
        elif lifecycle >= 60:
            minutes = lifecycle // 60
            return f"{minutes}分钟"
        else:
            return f"{lifecycle}秒"

    def _escape_json(self, text: str) -> str:
        """
        转义 JSON 字符串中的特殊字符。

        Args:
            text: 原始文本

        Returns:
            转义后的文本
        """
        if not text:
            return ""
        # 转义双引号和反斜杠
        text = text.replace("\\", "\\\\")
        text = text.replace('"', '\\"')
        # 替换换行符
        text = text.replace("\n", "\\n")
        text = text.replace("\r", "\\r")
        text = text.replace("\t", "\\t")
        return text

    def _parse_response(self, response: str) -> dict[str, Any]:
        """
        解析 LLM 响应，提取 JSON 结果。

        Args:
            response: LLM 响应文本

        Returns:
            解析后的字典，包含 analysis 和 items
        """
        json_str = self._extract_json(response)
        if not json_str:
            return {"analysis": "", "items": []}

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试修复常见格式问题
            try:
                # 尝试提取 items 数组
                items_match = re.search(r'"items"\s*:\s*\[(.*?)\]', json_str, re.DOTALL)
                if items_match:
                    items_str = "[" + items_match.group(1) + "]"
                    # 提取 id
                    ids = re.findall(r'"id"\s*:\s*"([^"]+)"', items_str)
                    if ids:
                        return {"analysis": "", "items": ids}

                # 尝试直接提取数组
                array_match = re.search(r'\[(.*?)\]', json_str, re.DOTALL)
                if array_match:
                    items_str = "[" + array_match.group(1) + "]"
                    ids = re.findall(r'"([^"]+)"', items_str)
                    if ids:
                        return {"analysis": "", "items": ids}
            except Exception:
                pass

            return {"analysis": "", "items": []}

    def _extract_json(self, text: str) -> str:
        """
        从文本中提取 JSON 对象字符串。

        Args:
            text: 原始文本

        Returns:
            JSON 对象字符串，如果未找到返回空字符串
        """
        # 查找第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or start >= end:
            return "{}"

        return text[start:end + 1]

    async def rank_with_analysis(
        self, query: str, items: list[dict[str, Any]], top_k: int = 5
    ) -> dict[str, Any]:
        """
        对记忆项进行 LLM 重排序，同时返回分析过程。

        Args:
            query: 用户查询字符串
            items: 记忆项列表
            top_k: 最多返回的结果数

        Returns:
            dict，包含：
            - analysis: str，分析过程
            - items: list[str]，排序后的 item_id 列表
        """
        if not items:
            return {"analysis": "没有可排序的记忆项", "items": []}

        if top_k <= 0:
            return {"analysis": "top_k 必须大于 0", "items": []}

        items_data = self._format_items(items)
        prompt = RANKER_PROMPT.format(
            query=query,
            items_data=items_data,
            top_k=top_k,
        )

        response = await self._call_llm(prompt)
        result = self._parse_response(response)

        return {
            "analysis": result.get("analysis", ""),
            "items": result.get("items", [])[:top_k],
        }