"""
查询重写器 (Query Rewriter)

将用户查询重写为自包含的、无歧义的版本，利用对话历史解析代词和隐含引用。
"""

import re
from openai import AsyncClient


from ..prompts.nl import QUERY_REWRITE_PROMPT


class QueryRewriter:
    """将用户查询重写为自包含的、无歧义的版本"""

    def __init__(self, llm_client: AsyncClient, model: str = "gpt-4o-mini"):
        """
        初始化查询重写器

        Args:
            llm_client: OpenAI AsyncClient 实例，与 mql_generator.py 中的接口保持一致
            model: LLM 模型名称，默认 "gpt-4o-mini"
        """
        self.llm = llm_client
        self.model = model

    async def rewrite(self, query: str, history: list[dict]) -> str:
        """
        将用户查询重写为自包含版本

        Args:
            query: 原始用户查询
            history: 对话历史列表，每项为 dict，包含 role 和 content 字段

        Returns:
            重写后的自包含查询字符串
        """
        formatted_history = self._format_history(history)
        prompt = QUERY_REWRITE_PROMPT.format(
            conversation_history=formatted_history,
            query=query
        )

        response = await self.llm.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )

        response_text = response.choices[0].message.content
        rewritten = self._extract_tag(response_text, "rewritten_query")
        return rewritten if rewritten else query

    def _format_history(self, history: list[dict]) -> str:
        """
        格式化对话历史为字符串

        Args:
            history: 对话历史列表

        Returns:
            格式化后的历史记录字符串
        """
        if not history:
            return "（无历史记录）"

        lines = []
        for turn in history[-5:]:  # 最近5轮对话
            role = turn.get("role", "user")
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _extract_tag(self, text: str, tag: str) -> str:
        """
        从文本中提取指定标签的内容

        Args:
            text: 原始文本
            tag: 标签名（如 "analysis"、"rewritten_query"）

        Returns:
            标签内的内容，如果未找到则返回空字符串
        """
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""
