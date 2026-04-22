# -*- coding: utf-8 -*-
"""
NL Pipeline 编排引擎 (NLSearchPipeline)

简化版流程：
1. 查询重写 - 解析代词和隐含引用
2. NL→MQL生成 - 将 NL 转为 MQL 语句
3. 执行MQL - 调用 MemorySystem.execute()
4. 混合重排 - 如果不够，扩展检索并重排
5. 生成NL回答 - 将记忆转为自然语言
6. 反思审查 - 判断 NL 结果是否足够回答问题
   - 足够 → 直接输出
   - 不够 → 重查MQL生成 → 返回步骤 3 重新执行

参考：MEMU_TEXT2MEM_REFERENCE.md 第十章
"""

from __future__ import annotations

from typing import Any

from .rewriter import QueryRewriter
from .mql_generator import MQLGenerator
from .hybrid_search import HybridSearch


# =============================================================================
# 全局 LLM 调用计数器（跨查询累计）
# =============================================================================

_llm_stats = {
    "total_calls": 0,          # 累计 LLM 调用次数
    "query_count": 0,           # 累计 NL 查询次数
    "calls_detail": [],         # 最近各次查询的调用明细
}


def get_llm_stats() -> dict:
    """获取 LLM 调用统计（全局）"""
    return _llm_stats.copy()


def reset_llm_stats():
    """重置 LLM 调用统计"""
    _llm_stats["total_calls"] = 0
    _llm_stats["query_count"] = 0
    _llm_stats["calls_detail"].clear()


# =============================================================================
# Prompt 模板
# =============================================================================

RESULT_GENERATION_PROMPT = """# Task
你是 {holder}，正在回忆自己的记忆来回答问题。你是在自问自答——用第一人称，从自己的视角出发。

# 问题
{query}

# 检索到的记忆（共 {count} 条）
{memories_text}

# 回答要求
1. 用第一人称回答，你就是 {holder}，这些是你的亲身记忆
2. 语气自然简练，像在心里默默回忆，不要用"根据记忆"、"用户"等旁观者措辞
3. 如果记忆为空，简短说"我暂时想不起相关的事"
4. 提炼记忆中的关键信息，用自己的话简要概括，不要逐条罗列
5. 涉及时间/日期时，换算为相对时间（如"昨天"、"上周"）更自然
6. 控制篇幅：回答不超过 5-8 句话，抓住重点即可，不要写长文
7. ★禁止输出动作描写★：不要写括号动作（如"（轻轻放下书）"、"（微笑）"等），这是内心回忆，不是舞台表演
"""

# Stage 6: 反思审查（简化版——只判断够不够，不给MQL建议）
REFLECTION_REVIEW_PROMPT = """# Task
你是一个记忆检索审核员。你的任务是判断 NL 回答是否足够回答用户的问题。

# 用户原始问题
{query}

# 当前 NL 回答
{nl_response}

# 检索到的记忆（共 {count} 条）
{memories_text}

# 判断标准
**足够（输出）**：检索到记忆且 NL 回答已实质性回答了问题

**不够（重查）**的典型场景：
1. 检索到 0 条记忆，但用户的问题不太可能是毫无记忆的 → subject 映射错误或 MQL 语法错误
2. NL 回答说"没有相关记忆"或"暂时想不起"但问题应该有记忆 → 检索失败
3. NL 回答极短（<5字）或只有标点 → 检索结果根本没有命中主题
4. NL 回答内容与问题明显不符 → 检索方向错了
5. 问题涉及多维度但 NL 回答单一 → 遗漏了某些方面的记忆
6. 记忆中有相关内容但 NL 回答没有涵盖 → 回答不完整

# 输出格式（严格遵循）
<retry>YES/NO</retry>
<hint>如果 retry=YES，用一句话说明哪里不够（不要给MQL建议，只要说明问题，如"内容太少"或"应该查用户的饮食习惯"）；如果 retry=NO 则写"无"</hint>
"""


# 重查MQL生成：解析反思提示，生成改进版MQL
REGENERATE_MQL_PROMPT = """# Task
你是一个 MQL 检索专家。用户刚刚进行了一次 NL 查询，但检索结果不理想，你需要根据反思提示重新生成 MQL。

# 当前时间
{current_date}

# 用户原始问题
{query}

# 上一次执行的 MQL
{prev_mql}

# 反思审查的提示
{reflection_hint}

# 重查要求
1. 仔细分析反思提示，理解问题所在
2. 结合用户原始问题，生成更合适的 MQL
3. 可以调整 subject/object/limit 等条件
4. 如果反思提示说"内容太少"，可以增加 LIMIT 或去掉严格限制
5. 如果反思提示说"应该查XX方面"，需要在 MQL 中体现这个方向
6. 必须生成合法的 MQL 语句
7. 涉及相对时间词时，根据当前时间换算为绝对年份/月份

# 输出格式（严格遵循）
<mql>改进后的 MQL 语句</mql>
<confidence>0.0-1.0 的置信度</confidence>
"""


class NLSearchPipeline:
    """
    NL 检索流水线（简化版，含循环重查）

    Attributes:
        STEPS: 流水线步骤列表，每项为 (step_id, description)
        llm: LLM 客户端
        mem: MemorySystem 实例
        rewriter: QueryRewriter 实例
        mql_gen: MQLGenerator 实例
        hybrid: HybridSearch 实例
        max_retries: 最大重查次数（默认 3）
    """

    STEPS = [
        ("rewrite", "查询重写"),
        ("nl_to_mql", "NL→MQL生成"),
        ("execute_mql", "执行MQL"),
        ("hybrid_rerank", "混合重排"),
        ("generate_nl", "生成NL回答"),
        ("reflection_review", "反思审查"),
    ]

    # 阶段 ID 常量（用于跳转）
    STAGE_REWRITE = 2
    STAGE_NL_TO_MQL = 3
    STAGE_EXECUTE_MQL = 4
    STAGE_HYBRID_RERANK = 5
    STAGE_GENERATE_NL = 6
    STAGE_REFLECTION = 7
    STAGE_REGENERATE_MQL = 8  # 新增：重查MQL生成

    def __init__(
        self,
        llm_client: Any,
        memory_system: Any,
        model: str = "gpt-4o-mini",
        debug: bool = False,
        max_retries: int = 3,
    ):
        """
        初始化 NL 检索流水线。

        Args:
            llm_client: LLM 客户端，需支持 async chat 方法
            memory_system: MemorySystem 实例，提供 execute() 方法
            model: LLM 模型名称
            debug: 是否开启调试输出
            max_retries: 最大重查次数（默认 3）
        """
        self.llm = llm_client
        self.mem = memory_system
        self.model = model
        self.debug = debug
        self.max_retries = max_retries
        self.holder = getattr(memory_system, "holder", "我")
        self.rewriter = QueryRewriter(llm_client, model=model)
        self.mql_gen = MQLGenerator(llm_client, model=model, debug=debug, system_holder=self.holder)
        self.hybrid = HybridSearch(memory_system)

    async def run(self, nl_query: str, history: list[dict], top_k: int = 10) -> dict:
        """
        执行完整的 NL → 检索流程（含循环重查）。

        流程：查询重写 → MQL生成 → 执行 → 混合重排 → 生成NL → 反思审查
              ↑（如果反思审查需要重查）──────────────────→ 重查MQL生成 → 执行 → 混合重排 → 生成NL → 反思审查 → ...

        Args:
            nl_query: 自然语言查询
            history: 对话历史列表，每项为 {"role": "user"/"assistant", "content": "..."}
            top_k: 检索返回的最大结果数（-1 表示由 LLM 自行决定）

        Returns:
            dict，包含：
            - type: "direct" 或 "retrieved"
            - response: 最终回答
            - mql: 生成的 MQL 语句
            - result: 检索结果列表
            - rewritten_query: 重写后的查询
            - llm_calls: 本次查询的 LLM 调用次数
            - steps_summary: 各步骤执行摘要
            - retry_count: 重查次数
        """
        # ----------------------------------------------------------------
        # 初始化全局状态
        # ----------------------------------------------------------------
        state: dict[str, Any] = {
            "query": nl_query,
            "history": history,
            "rewritten_query": nl_query,
            "mql": "",
            "mql_confidence": 0.0,
            "mql_fallback": False,
            "result": [],
        }
        all_steps_summary: list[str] = []
        llm_calls = 0
        retry_count = 0
        final_response = ""
        prev_mql = ""  # 记录上一次MQL，用于重查生成

        # ----------------------------------------------------------------
        # Stage 2: 查询重写（跳过预检索，直接开始）
        # ----------------------------------------------------------------
        state, llm_calls = await self._step2_rewrite(state, history, all_steps_summary, llm_calls)

        # ----------------------------------------------------------------
        # 主循环：最多 max_retries 次重查
        # ----------------------------------------------------------------
        next_stage = self.STAGE_NL_TO_MQL  # 首次从 MQL生成 开始

        while retry_count <= self.max_retries:
            retry_count += 1
            if retry_count > 1:
                all_steps_summary.append(f"↩ 重查 #{retry_count - 1}（跳转至 Stage {next_stage}）")

            # ----------------------------------------------------------------
            # Stage 3 / Stage 8: MQL生成 或 重查MQL生成
            # ----------------------------------------------------------------
            if next_stage <= self.STAGE_NL_TO_MQL:
                state, llm_calls, next_stage = await self._step3_nl_to_mql(
                    state, top_k, all_steps_summary, llm_calls
                )
                if next_stage != self.STAGE_NL_TO_MQL:
                    continue

            # ----------------------------------------------------------------
            # Stage 4: 执行MQL
            # ----------------------------------------------------------------
            state = self._step4_exec_mql(state, all_steps_summary)

            # ----------------------------------------------------------------
            # Stage 5: 混合重排
            # ----------------------------------------------------------------
            state, all_steps_summary = await self._step5_hybrid_rerank(
                state, top_k, all_steps_summary
            )

            # ----------------------------------------------------------------
            # Stage 6: 生成NL回答
            # ----------------------------------------------------------------
            nl_response, llm_calls, next_stage = await self._step6_generate_nl(
                state, all_steps_summary, llm_calls
            )
            if next_stage != self.STAGE_GENERATE_NL:
                continue

            # ----------------------------------------------------------------
            # Stage 7: 反思审查
            # ----------------------------------------------------------------
            review_result, llm_calls = await self._step7_reflection_review(
                state, nl_response, all_steps_summary, llm_calls
            )
            final_response = nl_response  # 默认用生成的回答

            if not review_result.get("retry"):
                # 足够，直接输出
                all_steps_summary.append(
                    f"7.反思审查→✅ 足够（{len(state['result'])}条记忆），输出结果"
                )
                _llm_stats["total_calls"] += llm_calls
                _llm_stats["query_count"] += 1
                _llm_stats["calls_detail"].append({
                    "query": nl_query,
                    "calls": llm_calls,
                    "retries": retry_count - 1,
                })
                return self._build_result(
                    type_="retrieved",
                    state=state,
                    llm_calls=llm_calls,
                    steps_summary=all_steps_summary,
                    retry_count=retry_count - 1,
                    nl_response=final_response,
                    final_mql=state.get("mql", ""),
                )

            # 需要重查
            reflection_hint = review_result.get("hint", "")
            prev_mql = state.get("mql", "")
            all_steps_summary.append(
                f"7.反思审查→🔄 需重查：{reflection_hint}"
            )

            # ----------------------------------------------------------------
            # Stage 8: 重查MQL生成（带反思提示）
            # ----------------------------------------------------------------
            state, llm_calls = await self._step8_regenerate_mql(
                state, top_k, prev_mql, reflection_hint, all_steps_summary, llm_calls
            )
            next_stage = self.STAGE_EXECUTE_MQL  # 重查MQL生成后，跳转执行

        # 达到最大重查次数
        all_steps_summary.append(f"⚠️ 达到最大重查次数（{self.max_retries}），返回最后一次结果")
        _llm_stats["total_calls"] += llm_calls
        _llm_stats["query_count"] += 1
        _llm_stats["calls_detail"].append({
            "query": nl_query,
            "calls": llm_calls,
            "retries": retry_count - 1,
        })
        return self._build_result(
            type_="retrieved",
            state=state,
            llm_calls=llm_calls,
            steps_summary=all_steps_summary,
            retry_count=retry_count - 1,
            nl_response=nl_response,
            final_mql=state.get("mql", ""),
        )

    # =========================================================================
    # 各阶段实现
    # =========================================================================

    async def _step2_rewrite(
        self,
        state: dict,
        history: list[dict],
        steps_summary: list[str],
        llm_calls: int,
    ) -> tuple[dict, int]:
        """Stage 2: 查询重写"""
        if history:
            state["query"] = await self.rewriter.rewrite(state["query"], history)
            state["rewritten_query"] = state["query"]
            llm_calls += 1
            steps_summary.append(f"2.查询重写→'{state['query']}'")
        else:
            steps_summary.append("2.查询重写→跳过（无历史）")
        return state, llm_calls

    async def _step3_nl_to_mql(
        self,
        state: dict,
        top_k: int,
        steps_summary: list[str],
        llm_calls: int,
    ) -> tuple[dict, int, int]:
        """Stage 3: NL→MQL生成"""
        mql_plan = await self.mql_gen.generate_with_fallback(state["query"], topk=top_k)
        state["mql"] = mql_plan["mql"]
        state["mql_confidence"] = mql_plan.get("confidence", 0.0)
        state["mql_fallback"] = mql_plan.get("fallback", False)
        llm_calls += 1
        use_graph = "GRAPH" in state["mql"].upper()
        steps_summary.append(
            f"3.MQL生成→{'GRAPH' if use_graph else '普通查询'} "
            f"(置信度={state['mql_confidence']:.2f}, "
            f"{'降级' if state['mql_fallback'] else '正常'})"
        )
        return state, llm_calls, self.STAGE_NL_TO_MQL

    def _step4_exec_mql(
        self, state: dict, steps_summary: list[str]
    ) -> dict:
        """Stage 4: 执行MQL"""
        result = self._exec_mql(state["mql"])
        state["result"] = result
        state["mql_raw_count"] = len(result)
        steps_summary.append(f"4.MQL执行→{len(result)} 条记忆")
        return state

    async def _step5_hybrid_rerank(
        self, state: dict, top_k: int, steps_summary: list[str]
    ) -> tuple[dict, list[str]]:
        """Stage 5: 混合重排"""
        if top_k < 0:
            steps_summary.append("5.混合重排→跳过（top_k=-1，LLM自行决定）")
        elif "GRAPH" not in state["mql"].upper():
            # 如果 MQL（TIME过滤）返回了0条，说明时间严格过滤无结果，
            # 保留0条而不是用向量搜索替换（单值TIME不应被绕过）
            if state.get("mql_raw_count", 0) == 0:
                steps_summary.append("5.混合重排→跳过（TIME严格过滤0条，不绕过）")
            else:
                reranked = await self.hybrid.search(state["query"], top_k=top_k)
                state["result"] = reranked
                steps_summary.append(f"5.混合重排→{len(reranked)} 条（向量搜索重排）")
        else:
            steps_summary.append("5.混合重排→跳过（GRAPH查询，保留图扩展结果）")
        return state, steps_summary

    async def _step6_generate_nl(
        self,
        state: dict,
        steps_summary: list[str],
        llm_calls: int,
    ) -> tuple[str, int, int]:
        """Stage 6: 生成NL回答"""
        nl_response = await self._generate_nl_response(state["query"], state["result"])
        llm_calls += 1
        steps_summary.append(f"6.生成NL回答→{len(nl_response)//50 if nl_response else 0}字")
        return nl_response, llm_calls, self.STAGE_GENERATE_NL

    async def _step7_reflection_review(
        self,
        state: dict,
        nl_response: str,
        steps_summary: list[str],
        llm_calls: int,
    ) -> tuple[dict, int]:
        """
        Stage 7: 反思审查（简化版）
        Returns:
            (review_result, llm_calls)
            - review_result: {"retry": bool, "hint": str}
        """
        review_result = await self._reflect_review(
            state["query"], nl_response, state["result"]
        )
        llm_calls += 1
        return review_result, llm_calls

    async def _step8_regenerate_mql(
        self,
        state: dict,
        top_k: int,
        prev_mql: str,
        reflection_hint: str,
        steps_summary: list[str],
        llm_calls: int,
    ) -> tuple[dict, int]:
        """
        Stage 8: 重查MQL生成（根据反思提示生成改进版MQL）
        跳转回 Stage 4 执行
        """
        mql_plan = await self._regenerate_mql(
            state["query"], prev_mql, reflection_hint, topk=top_k
        )
        state["mql"] = mql_plan["mql"]
        state["mql_confidence"] = mql_plan.get("confidence", 0.0)
        state["mql_fallback"] = mql_plan.get("fallback", False)
        llm_calls += 1
        steps_summary.append(
            f"8.重查MQL生成→'{state['mql']}' "
            f"(置信度={state['mql_confidence']:.2f}, 反思提示: {reflection_hint})"
        )
        return state, llm_calls

    # =========================================================================
    # 内部辅助方法
    # =========================================================================

    def _exec_mql(self, mql: str) -> list[dict]:
        """执行 MQL 语句并返回结果列表。"""
        try:
            mql_result = self.mem.execute(mql)
            if hasattr(mql_result, "data"):
                return mql_result.data or []
            elif isinstance(mql_result, list):
                return mql_result
            else:
                return []
        except Exception:
            return []

    async def _generate_nl_response(self, nl_query: str, results: list[dict]) -> str:
        """用 LLM 将检索结果转化为自然语言回答。"""
        if not results:
            return "暂时没有相关记忆。"

        # 截断记忆，避免 token 过多导致回答过长
        max_memories = 15
        if len(results) > max_memories:
            results = results[:max_memories]

        memories_text = self._build_memories_text(results)
        prompt = RESULT_GENERATION_PROMPT.format(
            holder=self.holder,
            query=nl_query,
            count=len(results),
            memories_text=memories_text,
        )
        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=512,
            )
            return resp.choices[0].message.content or "（生成失败）"
        except Exception as e:
            if self.debug:
                print(f"[ResultGenerator DEBUG] LLM error: {e}")
            return "（生成失败）"

    async def _reflect_review(
        self,
        nl_query: str,
        nl_response: str,
        results: list[dict],
    ) -> dict[str, Any]:
        """
        Stage 7: 反思审查——判断 NL 回答是否足够。

        Returns:
            {"retry": bool, "hint": str}
        """
        memories_text = self._build_memories_text(results)
        prompt = REFLECTION_REVIEW_PROMPT.format(
            query=nl_query,
            nl_response=nl_response,
            count=len(results),
            memories_text=memories_text,
        )
        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个记忆检索审核员，判断NL回答是否足够。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=256,
            )
            raw = resp.choices[0].message.content or ""
            retry_tag = self._extract_tag(raw, "retry")
            retry = "YES" in retry_tag.upper()
            hint = self._extract_tag(raw, "hint")
            return {
                "retry": retry,
                "hint": hint.strip() if hint else ("内容不足以回答问题" if retry else "无"),
                "raw": raw,
            }
        except Exception as e:
            if self.debug:
                print(f"[ReflectionReview DEBUG] LLM error: {e}")
            return {"retry": False, "hint": "无", "raw": ""}

    async def _regenerate_mql(
        self,
        nl_query: str,
        prev_mql: str,
        reflection_hint: str,
        topk: int = 10,
    ) -> dict[str, Any]:
        """
        Stage 8: 重查MQL生成——根据反思提示生成改进版MQL。
        """
        from datetime import datetime
        now = datetime.now()
        prompt = REGENERATE_MQL_PROMPT.format(
            query=nl_query,
            prev_mql=prev_mql,
            reflection_hint=reflection_hint,
            current_date=now.strftime("%Y-%m-%d %H:%M"),
        )
        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个MQL检索专家，根据反思提示生成改进的MQL查询。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=256,
            )
            raw = resp.choices[0].message.content or ""
            mql_text = self._extract_tag(raw, "mql")
            conf_text = self._extract_tag(raw, "confidence")
            # 防御性修复：如果 MQL 缺少 SELECT 前缀，自动补上
            mql_text = mql_text.strip() if mql_text else ""
            if mql_text and not mql_text.upper().startswith("SELECT"):
                mql_text = "SELECT * FROM memories " + mql_text
            try:
                confidence = float(conf_text.strip()) if conf_text else 0.0
            except ValueError:
                confidence = 0.0
            return {
                "mql": mql_text.strip() if mql_text else prev_mql,
                "confidence": confidence,
                "fallback": False,
                "raw": raw,
            }
        except Exception as e:
            if self.debug:
                print(f"[RegenerateMQL DEBUG] LLM error: {e}")
            return {"mql": prev_mql, "confidence": 0.0, "fallback": True, "raw": ""}

    @staticmethod
    def _extract_tag(text: str, tag: str) -> str:
        """从文本中提取 <tag>...</tag> 内容"""
        import re
        m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
        return m.group(1) if m else ""

    def _build_memories_text(self, results: list[dict], include_slots: bool = True) -> str:
        """构建记忆文本，供 LLM 使用。"""
        if not results:
            return "（无记忆）"
        lines = []
        for i, item in enumerate(results, 1):
            content = item.get("content", "") or item.get("query_sentence", "")
            if include_slots:
                qs = item.get("query_sentence", "")
                lines.append(f"[{i}] {content}" + (f" (槽位: {qs})" if qs else ""))
            else:
                lines.append(f"[{i}] {content}")
        return "\n".join(lines)

    def _build_result(
        self,
        type_: str,
        state: dict,
        llm_calls: int,
        steps_summary: list[str],
        retry_count: int,
        nl_response: str,
        final_mql: str,
    ) -> dict:
        """构建最终返回结果。"""
        global_calls = _llm_stats["total_calls"]
        global_queries = _llm_stats["query_count"]
        avg_calls = global_calls / global_queries if global_queries > 0 else llm_calls

        return {
            "type": type_,
            "response": nl_response,
            "mql": final_mql,
            "result": state["result"],
            "rewritten_query": state.get("rewritten_query", state["query"]),
            "llm_calls": llm_calls,
            "steps_summary": steps_summary,
            "retry_count": retry_count,
            "global_stats": {
                "total_calls": global_calls,
                "total_queries": global_queries,
                "avg_calls_per_query": round(avg_calls, 2),
            },
        }


# =============================================================================
# 便捷入口
# =============================================================================


async def run_nl_pipeline(
    nl_query: str,
    history: list[dict],
    llm_client: Any,
    memory_system: Any,
    model: str = "gpt-4o-mini",
    debug: bool = False,
) -> dict:
    """
    便捷入口：创建并运行 NL 检索流水线。
    """
    pipeline = NLSearchPipeline(llm_client, memory_system, model=model, debug=debug)
    return await pipeline.run(nl_query, history)
