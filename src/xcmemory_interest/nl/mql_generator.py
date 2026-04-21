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

# NOTE: {holder}、{query}、{topk_hint} 由 MQLGenerator.generate() 在运行时替换
NL_TO_MQL_PROMPT = """# Task
将自然语言查询转换为 MQL 语句。

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当用户说"我"、"我的"时，指的是持有者「{holder}」，应映射为 subject='{holder}'。
你是在帮助「{holder}」回忆和检索她的记忆。

# MQL 语法（基础）
SELECT * FROM memories WHERE [slot=value,...] [SEARCH TOPK n] [LIMIT n]
六槽格式：<time><subject><action><object><purpose><result>，缺槽用 <无> 占位

# 结果数量
{topk_hint}

# ★★★ GRAPH 图扩展语法（重要！★★★）
对于综合性、人格分析、关系探索类查询，使用 GRAPH 关键字做多跳关联扩展。

## 何时用 GRAPH（必须严格遵循）：
1. **综合性自我分析**："我是一个怎么样的人"、"我的性格特点"、"我有哪些特质"、"评价一下我自己"
2. **关系探索**："我和谁关系好"、"我和家人的关系"、"我和朋友们的互动"、"分析我和XX的关系"
3. **经历总结**："我经历过哪些重要的事"、"我的人生轨迹"、"我的成长历程"
4. **多维度探索**："帮我全面了解自己"、"关于我的一切"
5. **深层追问**（当前面查询结果少于3条时再用）：在基础查询后加 GRAPH EXPAND(HOPS 2)

## ★何时不用 GRAPH（反面教材）★ —— 这是新手常犯的错误！
以下情况**不要**用 GRAPH，只用普通 WHERE + LIMIT：
1. **日常习惯/行为查询**："我平时会干嘛"、"我平时喜欢做什么"、"我有哪些日常习惯"
   - 错误：WHERE subject='{holder}' GRAPH EXPAND(HOPS 2)（结果太杂，混入无关记忆）
   - 正确：WHERE subject='{holder}' LIMIT 15（直接返回相关记忆）
2. **具体话题回忆**："我记得关于Python的事"、"我和哥哥做过什么"  
   - 错误：GRAPH EXPAND（会扩散到无关领域）
   - 正确：WHERE subject='{holder}' AND object='Python' LIMIT 10
3. **身份/定义类问句**："哥哥是谁"、"XX是做什么的"
   - 错误：GRAPH EXPAND（这是简单事实查询）
   - 正确：WHERE '关键词' LIMIT 5
4. **兴趣爱好查询**："我喜欢什么"、"我的兴趣爱好"
   - 错误：GRAPH EXPAND（太泛，结果太多太杂）
   - 正确：WHERE [subject='{holder}', purpose='喜欢'] LIMIT 10 或 WHERE subject='{holder}' LIMIT 15

## GRAPH 操作类型
- **GRAPH EXPAND(HOPS n)**：从种子记忆出发，扩展 n 跳邻居（推荐 HOPS 2）
- **GRAPH CONNECTED(MIN_SHARED m)**：获取所有连通记忆（共享 m 个以上槽位）
- **GRAPH VALUE_CHAIN(SLOTS [槽位列表])**：沿槽位值链追踪
- **GRAPH NEIGHBORS(MIN_SHARED m)**：获取直接相邻记忆

## GRAPH 使用示例（正面）
- "我是一个怎么样的人" → WHERE subject='{holder}' GRAPH EXPAND(HOPS 2) LIMIT 20
- "关于我的一切" → WHERE subject='{holder}' GRAPH CONNECTED(MIN_SHARED 2) LIMIT 30
- "我的性格" → WHERE [subject='{holder}', purpose='性格'] GRAPH EXPAND(HOPS 2)
- "我和家人的关系" → WHERE [subject='{holder}', object='家'] GRAPH EXPAND(HOPS 1)

# 槽位规则
① time：<平时>(永久) | <少年期/童年>(永久) | <那天晚上/深夜/早上>(一天) | <YYYY-MM-DD>
② subject：执行或承受动作的角色。**代词映射**："我"/"我的"→'{holder}'，"你"→'你'，"他"→'他'
③ action（预定义）：<是><与><的><同意><拒绝><希望><遵循><发生于><发生><想><说><做>
④ object：action 的承受者
⑤ purpose：目的/原因/条件
⑥ result：结果/补充

# ★最重要★ 主体推断优先级
## 高优先级推断（直接判定）：
- "关于XX的记忆" → subject='{holder}'，object='XX'
- "我之前/以前/上次XX" → subject='{holder}'
- 纯粹时间/话题查询 → subject='{holder}'

## 身份/定义问句（特殊处理）：
- "XX是谁" / "XX是做什么的" → **不是 subject='{holder}'**，而是跨槽位搜索
  - "哥哥是谁" → WHERE '哥哥' LIMIT 5（在任意槽位搜索含"哥哥"关键词的记忆）
  - "XX是做什么的" → WHERE subject='{holder}' AND object='XX' LIMIT 5
- "XX是什么/怎么样" 且 XX 是具体人名/实体 → 跨槽位：WHERE 'XX' LIMIT 5

## 低优先级（明确指定了他者才用）：
- "查找XX的记忆" 且 XX 是具体人名 → subject='XX'
- "XX和YY的记忆" → subject='XX'

# 示例
- "关于Python的记忆" → WHERE subject='{holder}' AND object='Python'
- "查询我关于Python的记忆" → WHERE subject='{holder}' AND object='Python'
- "我是一个怎么样的人" → WHERE subject='{holder}' GRAPH EXPAND(HOPS 2) LIMIT 20
- "我想学Python" → WHERE [subject='{holder}', action='学', object='Python']
- "我平时会干嘛" → WHERE subject='{holder}' LIMIT 15（不用 GRAPH！）
- "哥哥是谁" → WHERE '哥哥' LIMIT 5（不用 GRAPH！跨槽位搜索身份相关记忆）

# ★★★ 跨槽位多关键字搜索（重要！★★★）
当用户询问**同时涉及多个主题或关键字**的记忆时，使用 bare string 跨槽位 AND 语法：
- 语法：WHERE '关键词1' AND '关键词2' AND '关键词3' ...
- 含义：每对单引号表示"在任意槽位中包含此关键词"，多个关键词用 AND 连接表示**全部满足**
- 优势：无需指定关键词在哪个槽位，系统自动遍历 6 个槽位做匹配

## 何时用跨槽位 AND（判断规则）
1. **多主题联合查询**：询问同时涉及多个概念的记忆
   - "记忆中有哪些同时提到 A 和 B 的事？"
   - "在关于 X 的记忆里，哪些也涉及 Y？"
2. **关系组合**：不明确指定 subject/object，但知道几个相关关键字
   - "既提到哥哥又提到绯绯的记忆" → WHERE '哥哥' AND '绯绯'
   - "关于慢慢来和哥哥的记忆" → WHERE '慢慢来' AND '哥哥'
3. **主题探索**：宽泛地探索某类话题，不确定在哪
   - "有没有提到某本书或某个人的记忆？"

## 跨槽位语法示例
- "既提到恋人又提到哥哥的记忆" → WHERE '恋人' AND '哥哥' LIMIT 20
- "关于慢慢来、牵手、哥哥的记忆" → WHERE '慢慢来' AND '牵手' AND '哥哥' LIMIT 20
- "星织相关的记忆中，哪些也提到了血缘" → WHERE subject='星织' AND '血缘' LIMIT 20

# 输出格式（必须严格遵循）
<analysis>意图+关键槽位+是否使用GRAPH及原因</analysis>
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

    def __init__(self, llm_client: Any, model: str = "gpt-4o-mini", debug: bool = False, system_holder: str = "我"):
        """
        初始化 MQL 生成器。

        Args:
            llm_client: LLM 客户端，需提供 async chat.completions.create() 方法
            model: LLM 模型名称，默认 "gpt-4o-mini"
            debug: 是否开启调试输出
            system_holder: 记忆系统持有者名称，NL 查询时用于将"我"映射到正确的人称
        """
        self.llm = llm_client
        self.model = model
        self.debug = debug
        self.system_holder = system_holder

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

    async def generate(self, nl_query: str, topk: int = None) -> dict[str, Any]:
        """
        将自然语言查询转换为 MQL 语句。

        Args:
            nl_query: 自然语言查询
            topk: 结果数量限制。不指定(None)时由LLM自行决定；指定数值时在prompt中告知LLM。

        Returns:
            dict，包含：
            - mql: str，生成的 MQL 语句
            - slots: dict，六槽字典
            - confidence: float，置信度 0.0-1.0
            - operation: str，操作类型（SELECT/INSERT/UPDATE/DELETE/hybrid_search）
        """
        # topk 提示：-1 表示由 LLM 自行决定；正数时告知 LLM 限制数量
        if topk is None or topk < 0:
            topk_hint = "（结果数量由你根据查询的宽泛程度自行判断，不在MQL中写死LIMIT）"
        else:
            topk_hint = f"（请在MQL的LIMIT子句中使用 {topk} 作为结果数量上限）"

        prompt = NL_TO_MQL_PROMPT.format(query=nl_query, holder=self.system_holder, topk_hint=topk_hint)
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

    async def generate_with_fallback(self, nl_query: str, threshold: float = 0.6, topk: int = None) -> dict[str, Any]:
        """
        置信度低于阈值时降级为纯向量搜索。

        Args:
            nl_query: 自然语言查询
            threshold: 置信度阈值，默认 0.6
            topk: 结果数量限制。不指定(None)或-1时由LLM自行决定

        Returns:
            置信度 >= threshold：正常 generate 结果
            置信度 < threshold：降级为纯向量搜索
        """
        result = await self.generate(nl_query, topk=topk)

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
