# -*- coding: utf-8 -*-
"""
NL Pipeline 编排引擎 (NLPipeline)

统一流程：
1. 意图识别 - 拆解为写入句/查询句 + lifecycle 档位判断
2a. 写入流程 - 写入句→INSERT MQL生成→执行（无NL输出）
2b. 查询流程 - 查询句→SELECT MQL生成→执行→混合重排→生成NL→反思审查
3. 拼接输出 - 多个查询的 NL 回答拼接

参考：MEMU_TEXT2MEM_REFERENCE.md 第十章
"""

from __future__ import annotations

import time
from typing import Any

from .intent_classifier import IntentClassifier
from .write_mql_generator import WriteMQLGenerator
from .mql_generator import MQLGenerator
from .hybrid_search import HybridSearch
from ..mql import Interpreter


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


class NLPipeline:
    """
    统一 NL Pipeline（意图识别 + 写入/查询分流）

    流程：
    1. 意图识别 → 拆解写入句/查询句
    2a. 写入句 → INSERT MQL → 执行（无NL输出）
    2b. 查询句 → SELECT MQL → 执行 → 混合重排 → NL生成 → 反思审查（含重查循环）
    3. 拼接多个查询的NL回答

    Attributes:
        llm: LLM 客户端
        mem: MemorySystem 实例
        intent_classifier: IntentClassifier 实例
        write_gen: WriteMQLGenerator 实例
        mql_gen: MQLGenerator 实例
        hybrid: HybridSearch 实例
        max_retries: 最大重查次数（默认 3）
    """

    def __init__(
        self,
        llm_client: Any,
        memory_system: Any,
        model: str = "gpt-4o-mini",
        debug: bool = False,
        max_retries: int = 3,
    ):
        self.llm = llm_client
        self.mem = memory_system
        self.model = model
        self.debug = debug
        self.max_retries = max_retries
        self.holder = getattr(memory_system, "holder", "我")
        self.intent_classifier = IntentClassifier(
            llm_client, model=model, system_holder=self.holder, debug=debug
        )
        self.write_gen = WriteMQLGenerator(
            llm_client, model=model, system_holder=self.holder, debug=debug
        )
        self.mql_gen = MQLGenerator(
            llm_client, model=model, debug=debug, system_holder=self.holder
        )
        self.hybrid = HybridSearch(memory_system)
        self._trace: list[dict] = []
        self._current_trace_step = ""

    def _make_traced_create(self):
        original = self.llm.chat.completions.create

        async def traced(model, messages, **kwargs):
            start = time.time()
            response = await original(model=model, messages=messages, **kwargs)
            elapsed_ms = (time.time() - start) * 1000
            usage = getattr(response, "usage", None)
            entry = {
                "step": self._current_trace_step,
                "model": model,
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                "output_preview": "",
                "duration_ms": round(elapsed_ms, 1),
            }
            if response.choices and response.choices[0].message.content:
                entry["output_preview"] = response.choices[0].message.content.strip()[:120]
            self._trace.append(entry)
            return response

        return traced

    async def run(self, nl_query: str, history: list[dict], top_k: int = 10) -> dict:
        """
        执行完整的 NL 流程（意图识别 + 写入/查询分流）。

        Args:
            nl_query: 自然语言输入
            history: 对话历史列表
            top_k: 检索返回的最大结果数（-1 表示由 LLM 自行决定）

        Returns:
            dict，包含：
            - type: "write_only" / "query_only" / "mixed" / "empty"
            - response: 最终回答（仅查询部分，写入无NL输出）
            - writes: 写入结果列表
            - queries: 各查询的结果列表
            - mql: 生成的所有 MQL 语句
            - llm_calls: 本次调用的 LLM 调用次数
            - steps_summary: 各步骤执行摘要
            - intent: 意图识别结果
        """
        all_steps_summary: list[str] = []
        llm_calls = 0
        self._trace = []
        original_create = self.llm.chat.completions.create
        self.llm.chat.completions.create = self._make_traced_create()

        # =====================================================================
        # Stage 1: 意图识别
        # =====================================================================
        self._current_trace_step = "① 意图识别"
        intent_result = await self.intent_classifier.classify(nl_query, history)
        llm_calls += 1
        writes = intent_result["writes"]
        queries = intent_result["queries"]
        lifecycle = intent_result["lifecycle"]
        reference_duration = intent_result["reference_duration"]

        all_steps_summary.append(
            f"1.意图识别→{len(writes)}条写入 + {len(queries)}条查询 "
            f"(lifecycle={lifecycle})"
        )

        if self.debug:
            print(f"[NLPipeline DEBUG] writes={writes}, queries={queries}, lifecycle={lifecycle}")

        # =====================================================================
        # Stage 2a: 写入流程（如有写入句）
        # =====================================================================
        write_results = []
        write_mql_list = []
        if writes:
            self._current_trace_step = "②a 写入MQL生成"
            write_mql_result = await self.write_gen.generate(
                writes, reference_duration=reference_duration
            )
            llm_calls += 1
            write_mql_script = write_mql_result["mql_script"]

            if write_mql_script:
                try:
                    interp = Interpreter()
                    interp.bind("mem", self.mem)
                    write_results = interp.execute_script(write_mql_script)
                except Exception as e:
                    if self.debug:
                        print(f"[NLPipeline DEBUG] write execute error: {e}")
                    write_results = []

                write_mql_list = [p.strip() for p in write_mql_script.split(";") if p.strip()]

            all_steps_summary.append(
                f"2a.写入流程→{len(write_mql_list)}条INSERT, "
                f"{sum(1 for r in write_results if getattr(r, 'type', '') == 'insert')}条成功"
            )

        # =====================================================================
        # Stage 2b: 查询流程（如有查询句）
        # =====================================================================
        query_nl_responses = []
        query_results_all = []
        query_mql_list = []

        if queries:
            # 为每个查询句单独走查询流程
            for qi, query_stmt in enumerate(queries):
                qi_label = f"Q{qi+1}" if len(queries) > 1 else ""
                nl_resp, q_results, q_mql, q_llm_calls = await self._run_query_flow(
                    query_stmt, top_k, all_steps_summary, qi_label
                )
                llm_calls += q_llm_calls
                query_nl_responses.append({
                    "query": query_stmt,
                    "response": nl_resp,
                    "results": q_results,
                    "mql": q_mql,
                })
                query_results_all.extend(q_results)
                query_mql_list.extend(p.strip() for p in q_mql.split(";") if p.strip())

        # =====================================================================
        # Stage 3: 拼接输出
        # =====================================================================
        # 类型判定
        has_writes = len(writes) > 0
        has_queries = len(queries) > 0
        if has_writes and has_queries:
            type_ = "mixed"
        elif has_writes:
            type_ = "write_only"
        elif has_queries:
            type_ = "query_only"
        else:
            type_ = "empty"

        # NL 回答拼接：只有查询部分有NL输出
        final_response = ""
        if query_nl_responses:
            if len(query_nl_responses) == 1:
                final_response = query_nl_responses[0]["response"]
            else:
                # 多查询拼接
                parts = []
                for qr in query_nl_responses:
                    if qr["response"].strip():
                        parts.append(qr["response"].strip())
                final_response = "\n\n".join(parts)

        # 所有 MQL
        all_mql = ";".join(write_mql_list + query_mql_list)

        # 统计
        _llm_stats["total_calls"] += llm_calls
        _llm_stats["query_count"] += 1
        _llm_stats["calls_detail"].append({
            "query": nl_query,
            "calls": llm_calls,
            "writes": len(writes),
            "queries": len(queries),
        })

        self.llm.chat.completions.create = original_create

        return {
            "type": type_,
            "response": final_response,
            "writes": write_results,
            "queries": query_nl_responses,
            "result": query_results_all,  # 兼容旧接口
            "mql": all_mql,
            "llm_calls": llm_calls,
            "steps_summary": all_steps_summary,
            "intent": {
                "writes": writes,
                "queries": queries,
                "lifecycle": lifecycle,
                "reference_duration": reference_duration,
            },
            "global_stats": {
                "total_calls": _llm_stats["total_calls"],
                "total_queries": _llm_stats["query_count"],
            },
            "trace": self._trace,
        }

    # =========================================================================
    # 查询流程（单个查询句的完整流程：MQL生成→执行→混合重排→NL→反思审查）
    # =========================================================================

    async def _run_query_flow(
        self,
        query_stmt: str,
        top_k: int,
        steps_summary: list[str],
        label: str = "",
    ) -> tuple[str, list[dict], str, int]:
        """
        单个查询句的完整流程（含重查循环）。

        Returns:
            (nl_response, results, mql, llm_calls)
        """
        llm_calls = 0
        retry_count = 0
        final_response = ""
        state: dict[str, Any] = {
            "query": query_stmt,
            "mql": "",
            "mql_confidence": 0.0,
            "mql_fallback": False,
            "result": [],
            "mql_raw_count": 0,
        }

        while retry_count <= self.max_retries:
            retry_count += 1

            if retry_count == 1:
                self._current_trace_step = f"{label}④ MQL生成"
                mql_plan = await self.mql_gen.generate_with_fallback(query_stmt, topk=top_k)
                llm_calls += 1
            else:
                self._current_trace_step = f"{label}⑧ 重查MQL生成"
                mql_plan = await self._regenerate_mql(
                    query_stmt, state["mql"], reflection_hint
                )
                llm_calls += 1
                steps_summary.append(
                    f"  {label}重查MQL→'{mql_plan.get('mql', '')}'"
                )

            state["mql"] = mql_plan["mql"]
            state["mql_confidence"] = mql_plan.get("confidence", 0.0)
            state["mql_fallback"] = mql_plan.get("fallback", False)

            # 执行 MQL
            state["result"] = self._exec_mql(state["mql"])
            state["mql_raw_count"] = len(state["result"])

            # 混合重排
            state, _ = await self._hybrid_rerank(state, top_k)

            self._current_trace_step = f"{label}⑤ NL生成"
            nl_response = await self._generate_nl_response(query_stmt, state["result"])
            llm_calls += 1

            self._current_trace_step = f"{label}⑥ 反思审查"
            review = await self._reflect_review(query_stmt, nl_response, state["result"])
            llm_calls += 1

            if not review.get("retry"):
                final_response = nl_response
                break

            reflection_hint = review.get("hint", "")
            if retry_count > self.max_retries:
                final_response = nl_response
                break

        return final_response, state["result"], state["mql"], llm_calls

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

    async def _hybrid_rerank(
        self, state: dict, top_k: int
    ) -> tuple[dict, str]:
        """混合重排"""
        if top_k < 0:
            return state, "跳过（top_k=-1）"
        elif "GRAPH" not in state["mql"].upper():
            # TIME 严格过滤0条时不绕过
            if state.get("mql_raw_count", 0) == 0:
                return state, "跳过（TIME严格过滤0条，不绕过）"
            else:
                reranked = await self.hybrid.search(state["query"], top_k=top_k)
                state["result"] = reranked
                return state, f"{len(reranked)}条"
        else:
            return state, "跳过（GRAPH查询）"

    async def _generate_nl_response(self, nl_query: str, results: list[dict]) -> str:
        """用 LLM 将检索结果转化为自然语言回答。"""
        if not results:
            return "暂时没有相关记忆。"

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
                print(f"[NLPipeline DEBUG] NL generation error: {e}")
            return "（生成失败）"

    async def _reflect_review(
        self,
        nl_query: str,
        nl_response: str,
        results: list[dict],
    ) -> dict[str, Any]:
        """反思审查——判断 NL 回答是否足够。"""
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
                print(f"[NLPipeline DEBUG] reflection error: {e}")
            return {"retry": False, "hint": "无", "raw": ""}

    async def _regenerate_mql(
        self,
        nl_query: str,
        prev_mql: str,
        reflection_hint: str,
    ) -> dict[str, Any]:
        """重查 MQL 生成。"""
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
                print(f"[NLPipeline DEBUG] regenerate MQL error: {e}")
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


# =============================================================================
# 向后兼容：NLSearchPipeline 委托给 NLPipeline
# =============================================================================

class NLSearchPipeline(NLPipeline):
    """向后兼容类，行为与 NLPipeline 一致。"""
    pass


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
    """便捷入口：创建并运行 NL Pipeline。"""
    pipeline = NLPipeline(llm_client, memory_system, model=model, debug=debug)
    return await pipeline.run(nl_query, history)
