# -*- coding: utf-8 -*-
"""
NL → MQL 生成器

将自然语言查询翻译为 MQL (Memory Query Language) 语句。
严格遵循 MQL规范.md 的六槽书写规范。
"""

from __future__ import annotations

import json
import re
from typing import Any

# =============================================================================
# Prompt 模板
# =============================================================================

NL_TO_MQL_PROMPT = """# Task
将自然语言查询转换为 MQL 语句。

# MQL 语法
SELECT * FROM memories WHERE [slot=value,...] [SEARCH TOPK n] [LIMIT n]
六槽格式：<time><subject><action><object><purpose><result>，缺槽用 <无> 占位

# 槽位规则（简版）
① time：<平时>(永久) | <少年期/童年>(永久) | <那天晚上/深夜/早上>(一天) | <YYYY-MM-DD>
② subject：执行或承受动作的角色。**代词原文保留**："我"→'我'，"你"→'你'，"他"→'他'
③ action（预定义）：<是><与><的><同意><拒绝><希望><遵循><发生于><发生><想><说><做>
④ object：action 的承受者
⑤ purpose：目的/原因/条件
⑥ result：结果/补充

# 代词展开规则
- "查询我有关的记忆" → WHERE subject='我'
- "查找关于XX的记忆" → WHERE subject='XX'
- "XX和我/他/她" → WHERE subject='我/他/她'

# 示例
- "查询我关于Python的记忆" → SELECT * FROM memories WHERE subject='我' LIMIT 10
- "查找星织的记忆" → SELECT * FROM memories WHERE subject='星织' LIMIT 10
- "我想学Python" → SELECT * FROM memories WHERE [subject='我', action='学'] SEARCH TOPK 5

# 输出格式（必须严格遵循）
<analysis>意图+关键槽位</analysis>
<mql>生成的MQL语句</mql>
<slots>{{"time":"","subject":"","action":"","object":"","purpose":"","result":""}}</slots>
<confidence>0.0-1.0</confidence>

# Input
自然语言查询: {query}
"""

# =============================================================================
# MQLGenerator
# =============================================================================


class MQLGenerator:
    """
    将自然语言查询翻译为 MQL 语句。

    Attributes:
        llm: OpenAI API 客户端（需支持 async chat 方法）
    """

    def __init__(self, llm_client: Any, model: str = "gpt-4o-mini", debug: bool = False):
        """
        初始化 MQL 生成器。

        Args:
            llm_client: LLM 客户端，需提供 async chat.completions.create() 方法
            model: LLM 模型名称，默认 "gpt-4o-mini"
            debug: 是否开启调试输出
        """
        self.llm = llm_client
        self.model = model
        self.debug = debug

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
            max_tokens=1024,
        )
        raw = response.choices[0].message.content or ""
        if self.debug:
            print(f"\n[MQLGenerator DEBUG] LLM raw output:\n{raw}\n[/MQLGenerator DEBUG]\n")
            # 同时打印解析结果，验证是否正确解析
            analysis = self._extract_tag(raw, "analysis")
            mql = self._extract_tag(raw, "mql")
            slots_raw = self._extract_tag(raw, "slots")
            confidence_raw = self._extract_tag(raw, "confidence")
            try:
                conf = float(confidence_raw) if confidence_raw else 0.5
            except (ValueError, TypeError):
                conf = 0.5
            print(f"[MQLGenerator DEBUG] parsed -> mql={mql!r}, confidence={conf}, slots={slots_raw!r}")
        return raw

    async def generate(self, nl_query: str) -> dict[str, Any]:
        """
        将自然语言查询转换为 MQL 语句。

        Args:
            nl_query: 自然语言查询

        Returns:
            dict，包含：
            - mql: str，生成的 MQL 语句
            - slots: dict，六槽字典
            - confidence: float，置信度 0.0-1.0
            - operation: str，操作类型（SELECT/INSERT/UPDATE/DELETE/hybrid_search）
        """
        prompt = NL_TO_MQL_PROMPT.format(query=nl_query)
        response = await self._call_llm(prompt)

        mql = self._extract_tag(response, "mql")
        slots = self._parse_json(self._extract_tag(response, "slots"))
        confidence_str = self._extract_tag(response, "confidence")
        analysis = self._extract_tag(response, "analysis")

        try:
            confidence = float(confidence_str) if confidence_str else 0.5
        except (ValueError, TypeError):
            # LLM 输出格式错乱（如双闭合标签），尝试从原始文本中提取第一个数字
            import re as _re
            nums = _re.findall(r"[-+]?\d*\.?\d+", confidence_str or "")
            confidence = float(nums[0]) if nums else 0.5

        operation = self._extract_operation(analysis, mql)

        return {
            "mql": mql,
            "slots": slots or {},
            "confidence": confidence,
            "operation": operation,
        }

    async def generate_with_fallback(self, nl_query: str, threshold: float = 0.6) -> dict[str, Any]:
        """
        置信度低于阈值时降级为纯向量搜索。

        Args:
            nl_query: 自然语言查询
            threshold: 置信度阈值，默认 0.6

        Returns:
            置信度 >= threshold：正常 generate 结果
            置信度 < threshold：降级为纯向量搜索
        """
        result = await self.generate(nl_query)

        if self.debug:
            print(f"[MQLGenerator DEBUG] generate_with_fallback final: mql={result['mql']!r}, confidence={result['confidence']}, fallback={result.get('fallback', False)}")

        if result["confidence"] < threshold:
            return {
                "mql": "SELECT * FROM memories SEARCH TOPK 10",
                "slots": {},
                "confidence": result["confidence"],
                "operation": "hybrid_search",
                "fallback": True,
                "original_mql": result["mql"],
            }

        result["fallback"] = False
        return result

    # -------------------------------------------------------------------------
    # 内部工具方法
    # -------------------------------------------------------------------------

    def _extract_tag(self, text: str, tag: str) -> str:
        """
        从响应文本中提取指定 XML 标签内容。

        Args:
            text: 原始响应文本
            tag: 标签名（不含 <>）

        Returns:
            标签内容，如果未找到则返回空字符串
        """
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _parse_json(self, json_str: str) -> dict[str, Any]:
        """
        解析 JSON 字符串，容忍格式错误。

        Args:
            json_str: JSON 字符串

        Returns:
            解析后的字典，解析失败返回空字典
        """
        if not json_str:
            return {}

        # 尝试直接解析
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 尝试修复常见格式问题
        try:
            # 移除单引号改为双引号（不完美但够用）
            fixed = json_str.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 尝试提取 JSON 对象
        try:
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(json_str[start:end])
        except json.JSONDecodeError:
            pass

        return {}

    def _extract_operation(self, analysis: str, mql: str) -> str:
        """
        从分析文本或 MQL 语句中提取操作类型。

        Args:
            analysis: 分析文本
            mql: 生成的 MQL 语句

        Returns:
            操作类型：SELECT / INSERT / UPDATE / DELETE / hybrid_search
        """
        # 优先从 MQL 语句判断
        mql_upper = mql.upper()
        if "SELECT" in mql_upper:
            return "SELECT"
        if "INSERT" in mql_upper:
            return "INSERT"
        if "UPDATE" in mql_upper:
            return "UPDATE"
        if "DELETE" in mql_upper:
            return "DELETE"

        # 从分析文本推断
        analysis_lower = analysis.lower()
        if any(kw in analysis_lower for kw in ["回忆", "查找", "搜索", "查询", "记得", "知道"]):
            return "SELECT"
        if any(kw in analysis_lower for kw in ["写入", "记录", "记住", "保存"]):
            return "INSERT"
        if any(kw in analysis_lower for kw in ["更新", "修改", "改变"]):
            return "UPDATE"
        if any(kw in analysis_lower for kw in ["删除", "忘掉"]):
            return "DELETE"

        return "SELECT"  # 默认为查询


# =============================================================================
# LLM Client 示例
# =============================================================================


class LLMClient:
    """
    OpenAI API 异步客户端示例。

    Usage:
        client = LLMClient(api_key="sk-...", model="gpt-4o")
        generator = MQLGenerator(client)
        result = await generator.generate("我之前学 Python 时遇到什么问题来着")
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
        发送聊天请求。

        Args:
            prompt: 用户 prompt
            system: 系统 prompt（可选）

        Returns:
            模型生成的文本内容
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
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
