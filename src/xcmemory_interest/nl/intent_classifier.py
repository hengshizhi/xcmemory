# -*- coding: utf-8 -*-
"""
意图识别器 (Intent Classifier)

将用户自然语言输入拆解为写入/查询意图，生成槽位友好的陈述句。
替代原 QueryRewriter，同时承担意图分流和代词消解的职责。

输出格式：
  <writes>写入陈述1|写入陈述2|...</writes>
  <queries>查询陈述1|查询陈述2|...</queries>
  <lifecycle>记忆档位</lifecycle>
"""

from __future__ import annotations

import re
from typing import Any


from ..prompts.nl import INTENT_CLASSIFY_PROMPT

# =============================================================================
# 档位 → reference_duration 映射
# =============================================================================

LIFECYCLE_TIERS = {
    "permanent": 999999,   # LIFECYCLE_INFINITY
    "long": 30 * 86400,    # 30 天
    "medium": 7 * 86400,   # 7 天
    "short": 86400,        # 1 天（默认）
}


# =============================================================================
# IntentClassifier
# =============================================================================


class IntentClassifier:
    """
    意图识别器：将用户输入拆解为写入句和查询句。

    替代原 QueryRewriter，同时承担：
    1. 意图分类（写入/查询）
    2. 代词消解
    3. 多句拆解
    4. 生命周期档位判断
    """

    def __init__(
        self,
        llm_client: Any,
        model: str = "gpt-4o-mini",
        system_holder: str = "我",
        debug: bool = False,
    ):
        self.llm = llm_client
        self.model = model
        self.system_holder = system_holder
        self.debug = debug

    async def classify(self, query: str, history: list[dict] | None = None) -> dict[str, Any]:
        """
        对用户输入进行意图分类。

        Args:
            query: 用户原始输入
            history: 对话历史（可选，用于消解代词）

        Returns:
            {
                "writes": [str, ...],      # 写入陈述句列表
                "queries": [str, ...],     # 查询陈述句列表
                "lifecycle": str,          # 档位: permanent/long/medium/short
                "reference_duration": int, # 档位对应的秒数
                "raw": str,               # LLM 原始输出
            }
        """
        from datetime import datetime
        now = datetime.now()

        # 如果有历史，拼接到 query 前面提供上下文
        context_query = query
        if history:
            context = self._format_history(history)
            context_query = f"[对话背景]\n{context}\n\n[当前输入]\n{query}"

        prompt = INTENT_CLASSIFY_PROMPT.format(
            query=context_query,
            holder=self.system_holder,
            current_date=now.strftime("%Y-%m-%d %H:%M"),
        )

        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2048,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as e:
            if self.debug:
                print(f"[IntentClassifier DEBUG] LLM error: {e}")
            # 降级：默认当作查询
            return {
                "writes": [],
                "queries": [query],
                "lifecycle": "short",
                "reference_duration": LIFECYCLE_TIERS["short"],
                "raw": "",
            }

        if self.debug:
            print(f"[IntentClassifier DEBUG] raw output:\n{raw}\n")

        # 解析输出
        writes_raw = self._extract_tag(raw, "writes")
        queries_raw = self._extract_tag(raw, "queries")
        lifecycle_raw = self._extract_tag(raw, "lifecycle").strip().lower()

        writes = [s.strip() for s in writes_raw.split("|") if s.strip()]
        queries = [s.strip() for s in queries_raw.split("|") if s.strip()]

        # Fallback A: LLM 输出被截断，tags 未闭合（常见于 max_tokens 不够用）
        if not writes and not queries and raw.strip() and raw.strip().startswith("<"):
            raw_stripped = raw.strip()
            # 尝试从截断的 <writes> 中提取非空内容
            partial = re.search(r"<writes>\s*([\s\S]*?)(?:</writes>|</|$)", raw_stripped)
            if partial:
                writes_text = partial.group(1).strip()
                writes = [s.strip() for s in writes_text.split("|") if s.strip()]
            # 尝试从截断的 <queries> 中提取
            partial_q = re.search(r"<queries>\s*([\s\S]*?)(?:</queries>|</|$)", raw_stripped)
            if partial_q:
                queries_text = partial_q.group(1).strip()
                queries = [s.strip() for s in queries_text.split("|") if s.strip()]
            # 尝试 lifecycle（可能被截断）
            partial_l = re.search(r"<lifecycle>\s*(\w+)(?:</lifecycle>|</|$)", raw_stripped)
            if partial_l:
                lifecycle_raw = partial_l.group(1).strip().lower()
            if self.debug:
                print(f"[IntentClassifier DEBUG] truncated fallback: writes={writes}, queries={queries}, lifecycle={lifecycle_raw}")

        # Fallback B: LLM 未使用 XML 标签时，将原始输出作为写入陈述
        if not writes and not queries and raw.strip():
            raw_stripped = raw.strip()
            # 尝试将非标签的纯文本首行作为写入陈述
            if "\n" in raw_stripped:
                first_line = raw_stripped.split("\n")[0].strip()
                if first_line and not first_line.startswith("<"):
                    writes = [first_line]
                    lifecycle_raw = "short"
            elif not raw_stripped.startswith("<"):
                writes = [raw_stripped]
                lifecycle_raw = "short"
            if self.debug:
                print(f"[IntentClassifier DEBUG] fallback: treating raw as write → {writes}")

        # 档位校验
        if lifecycle_raw not in LIFECYCLE_TIERS:
            lifecycle_raw = "short"
        reference_duration = LIFECYCLE_TIERS[lifecycle_raw]

        return {
            "writes": writes,
            "queries": queries,
            "lifecycle": lifecycle_raw,
            "reference_duration": reference_duration,
            "raw": raw,
        }

    # -------------------------------------------------------------------------
    # 内部方法
    # -------------------------------------------------------------------------

    def _format_history(self, history: list[dict]) -> str:
        """格式化对话历史"""
        if not history:
            return ""
        lines = []
        for turn in history[-5:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_tag(text: str, tag: str) -> str:
        """从文本中提取 <tag>...</tag> 内容"""
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""
