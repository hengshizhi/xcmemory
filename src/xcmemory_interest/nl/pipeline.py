# -*- coding: utf-8 -*-
"""
NL Pipeline 编排引擎 (NLSearchPipeline)

将自然语言查询转换为记忆检索的完整流水线，包括：
1. 预检索判断 - 判断是否需要检索
2. 查询重写 - 解析代词和隐含引用
3. NL→MQL生成 - 将 NL 转为 MQL 语句
4. 执行MQL - 调用 MemorySystem.execute()
5. 充分性检查 - 判断检索结果是否足够
6. 混合重排 - 如果不够，扩展检索并重排

参考：MEMU_TEXT2MEM_REFERENCE.md 第十章
"""

from __future__ import annotations

from typing import Any

from .decision import NLQueryDecider
from .rewriter import QueryRewriter
from .mql_generator import MQLGenerator
from .hybrid_search import HybridSearch


RESULT_GENERATION_PROMPT = """# Task
你是一个记忆管家，根据检索到的记忆，用自然语言回答用户的问题。

# 用户原始问题
{query}

# 检索到的记忆（共 {count} 条）
{memories_text}

# 回答要求
1. 用自然语言总结这些记忆，语气像在回忆往事
2. 如果记忆为空，诚实说"暂时没有相关记忆"
3. 每条记忆用一句话概括，突出关键信息
4. 回答要像在与用户对话，不要罗列数据
5. 涉及时间/日期时，换算为相对时间（如"昨天"、"上周"）更自然
"""


class NLSearchPipeline:
    """
    NL 检索流水线（简化版 WorkflowStep 模式）

    Attributes:
        STEPS: 流水线步骤列表，每项为 (step_id, description)
        llm: LLM 客户端
        mem: MemorySystem 实例
        decider: NLQueryDecider 实例
        rewriter: QueryRewriter 实例
        mql_gen: MQLGenerator 实例
        sufficiency: SufficiencyChecker 实例
        hybrid: HybridSearch 实例
    """

    STEPS = [
        ("pre_decision", "预检索判断"),
        ("rewrite", "查询重写"),
        ("nl_to_mql", "NL→MQL生成"),
        ("execute_mql", "执行MQL"),
        ("hybrid_rerank", "混合重排"),
    ]

    def __init__(self, llm_client: Any, memory_system: Any, model: str = "gpt-4o-mini", debug: bool = False):
        """
        初始化 NL 检索流水线。

        Args:
            llm_client: LLM 客户端，需支持 async chat 方法
            memory_system: MemorySystem 实例，提供 execute() 方法
            model: LLM 模型名称
            debug: 是否开启调试输出
        """
        self.llm = llm_client
        self.mem = memory_system
        self.model = model
        self.debug = debug
        # 初始化各组件
        self.decider = NLQueryDecider(llm_client, model=model)
        self.rewriter = QueryRewriter(llm_client, model=model)
        self.mql_gen = MQLGenerator(llm_client, model=model, debug=debug)
        self.hybrid = HybridSearch(memory_system)

    async def run(self, nl_query: str, history: list[dict], top_k: int = 10) -> dict:
        """
        执行完整的 NL → 检索流程。

        Args:
            nl_query: 自然语言查询
            history: 对话历史列表，每项为 {"role": "user"/"assistant", "content": "..."}
            top_k: 检索返回的最大结果数

        Returns:
            dict，包含：
            - type: "direct" 或 "retrieved"
            - response: 直接回复（NO_RETRIEVE 时）
            - mql: 生成的 MQL 语句
            - result: 检索结果列表
            - rewritten_query: 重写后的查询
        """
        state: dict[str, Any] = {"query": nl_query, "history": history}

        # =====================================================================
        # Step 1: 预检索判断
        # =====================================================================
        need_retrieve, state["query"] = await self.decider.decide(
            state["query"], history
        )
        if not need_retrieve:
            return {
                "type": "direct",
                "response": state["query"],
                "mql": "",
                "result": [],
                "rewritten_query": nl_query,
            }

        # =====================================================================
        # Step 2: 查询重写（仅在有历史时执行，节省一次 LLM 调用）
        # =====================================================================
        state["query"] = await self.rewriter.rewrite(state["query"], history) if history else state["query"]

        # =====================================================================
        # Step 3: NL→MQL 生成
        # =====================================================================
        mql_plan = await self.mql_gen.generate_with_fallback(state["query"])
        state["mql"] = mql_plan["mql"]
        state["mql_confidence"] = mql_plan.get("confidence", 0.0)
        state["mql_fallback"] = mql_plan.get("fallback", False)

        # =====================================================================
        # Step 4: 执行 MQL
        # =====================================================================
        result = self._exec_mql(state["mql"])
        state["result"] = result

        # =====================================================================
        # Step 5: 混合重排
        # 如果 MQL 包含 GRAPH 子句，说明图扩展已执行完毕，保留图结果不做覆盖
        # 否则用 HybridSearch 重排以提升普通向量搜索质量
        # =====================================================================
        if "GRAPH" not in state["mql"].upper():
            reranked = await self.hybrid.search(state["query"], top_k=top_k)
            state["result"] = reranked

        # =====================================================================
        # Step 6: LLM 生成自然语言回答
        # =====================================================================
        nl_response = await self._generate_nl_response(nl_query, state["result"])

        return {
            "type": "retrieved",
            "response": nl_response,
            "mql": state["mql"],
            "result": state["result"],
            "rewritten_query": state["query"],
        }

    # =========================================================================
    # 内部辅助方法
    # =========================================================================

    def _exec_mql(self, mql: str) -> list[dict]:
        """
        执行 MQL 语句并返回结果列表。

        Args:
            mql: MQL 语句

        Returns:
            结果列表，每项为 dict
        """
        try:
            mql_result = self.mem.execute(mql)
            # MQLResult 可能有 data 属性或直接是列表
            if hasattr(mql_result, "data"):
                return mql_result.data or []
            elif isinstance(mql_result, list):
                return mql_result
            else:
                return []
        except Exception:
            return []

    async def _generate_nl_response(self, nl_query: str, results: list[dict]) -> str:
        """
        用 LLM 将检索结果转化为自然语言回答。

        Args:
            nl_query: 原始 NL 查询
            results: 检索结果列表

        Returns:
            自然语言回答字符串
        """
        if not results:
            return "暂时没有相关记忆。"

        # 构建记忆文本
        memories_text = []
        for i, item in enumerate(results, 1):
            content = item.get("content", "")
            qs = item.get("query_sentence", "")
            memories_text.append(f"[{i}] {content} (槽位: {qs})")
        memories_text = "\n".join(memories_text)

        prompt = RESULT_GENERATION_PROMPT.format(
            query=nl_query,
            count=len(results),
            memories_text=memories_text,
        )

        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1024,
            )
            return resp.choices[0].message.content or "（生成失败）"
        except Exception as e:
            if self.debug:
                print(f"[ResultGenerator DEBUG] LLM error: {e}")
            return "（生成失败）"


# =============================================================================
# 便捷入口
# =============================================================================


async def run_nl_pipeline(
    nl_query: str,
    history: list[dict],
    llm_client: Any,
    memory_system: Any,
) -> dict:
    """
    便捷入口：创建并运行 NL 检索流水线。

    Args:
        nl_query: 自然语言查询
        history: 对话历史
        llm_client: LLM 客户端
        memory_system: MemorySystem 实例

    Returns:
        流水线执行结果
    """
    pipeline = NLSearchPipeline(llm_client, memory_system)
    return await pipeline.run(nl_query, history)
