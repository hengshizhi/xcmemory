"""
预检索判断模块 (Pre-Retrieval Decision)

判断自然语言查询是否需要触发记忆检索，避免每次 NL 都走检索流程，节省 token。

参考：MemU src/memu/prompts/retrieve/pre_retrieval_decision.py
"""

import re
from typing import AsyncIterator

from openai import AsyncOpenAI


from ..prompts.nl import PRE_RETRIEVAL_SYSTEM, USER_PROMPT_TEMPLATE


class NLQueryDecider:
    """
    判断自然语言查询是否需要触发记忆检索。

    使用 LLM 做预检索判断，避免每次 NL 都走检索流程，节省 token。
    当查询涉及历史记忆、用户偏好或需要回忆特定信息时，返回 RETRIEVE。
    当查询仅为寒暄、简单回应或常识性问题时，返回 NO_RETRIEVE。
    """

    def __init__(self, llm_client: AsyncOpenAI, model: str = "gpt-4o-mini"):
        """
        初始化预检索判断器。

        Args:
            llm_client: OpenAI AsyncClient 实例，与 mql_generator.py 保持一致
            model: LLM 模型名称，默认 "gpt-4o-mini"
        """
        self.llm = llm_client
        self.model = model

    async def decide(
        self,
        query: str,
        history: list[dict],
        retrieved_content: str = "（暂无）",
    ) -> tuple[bool, str]:
        """
        判断查询是否需要触发记忆检索。

        Args:
            query: 当前自然语言查询
            history: 对话历史，格式为 [{"role": "user"/"assistant", "content": "..."}]
            retrieved_content: 已检索到的内容（用于判断是否需要扩展检索）

        Returns:
            (需要检索, 重写后的查询)
            - 需要检索: True 表示需要走记忆检索流程，False 表示直接回答
            - 重写后的查询: 融入上下文的重写查询
        """
        formatted_history = self._format_history(history)
        prompt = f"{PRE_RETRIEVAL_SYSTEM}\n\n{USER_PROMPT_TEMPLATE.format(
            conversation_history=formatted_history,
            query=query,
            retrieved_content=retrieved_content,
        )}"

        response = await self._call_llm(prompt)
        decision = self._extract_tag(response, "decision")
        rewritten = self._extract_tag(response, "rewritten_query")

        needs_retrieval = decision.strip().upper() == "RETRIEVE"
        final_query = rewritten.strip() if rewritten else query

        return (needs_retrieval, final_query)

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
            messages=[
                {
                    "role": "system",
                    "content": "你是一个记忆检索判断助手，负责判断用户查询是否需要从记忆系统中检索信息。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=10000,
        )
        return response.choices[0].message.content or ""

    def _format_history(self, history: list[dict]) -> str:
        """
        格式化对话历史为字符串。

        Args:
            history: 对话历史列表

        Returns:
            格式化后的历史记录字符串，最多保留最近5轮
        """
        if not history:
            return "（无历史记录）"

        lines = []
        for turn in history[-5:]:  # 最近5轮
            role = turn.get("role", "user")
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _extract_tag(self, text: str, tag: str) -> str:
        """
        从 LLM 响应中提取指定标签的内容。

        Args:
            text: LLM 生成的原始响应
            tag: 标签名（如 "decision", "rewritten_query"）

        Returns:
            标签包裹的内容，如果未找到则返回空字符串
        """
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""


async def simple_decision_example():
    """
    简单示例：演示 NLQueryDecider 的基本用法。

    使用方式：
        from src.xcmemory_interest.nl.decision import simple_decision_example
        await simple_decision_example()
    """
    # 创建 LLM 客户端
    client = AsyncOpenAI(api_key="your-api-key")

    # 初始化判断器
    decider = NLQueryDecider(client)

    # 示例查询
    test_cases = [
        ("我之前学 Python 时遇到什么问题来着？", []),
        ("今天天气怎么样？", []),
        ("记得上周我们讨论的内容吗？", [{"role": "user", "content": "我想学Python"}]),
        ("你好啊！", []),
        ("他是如何看待咖啡的？", []),
    ]

    print("=" * 60)
    print("NLQueryDecider 预检索判断示例")
    print("=" * 60)

    for query, history in test_cases:
        needs_retrieval, rewritten = await decider.decide(query, history)
        decision_str = "RETRIEVE" if needs_retrieval else "NO_RETRIEVE"
        print(f"\n[Query] {query}")
        print(f"[Decision] {decision_str}")
        print(f"[Rewritten] {rewritten}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import asyncio

    asyncio.run(simple_decision_example())
