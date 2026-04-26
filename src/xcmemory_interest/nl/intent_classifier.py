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


# =============================================================================
# Prompt 模板
# =============================================================================

INTENT_CLASSIFY_PROMPT = """# Task
分析用户的自然语言输入，识别其中的**写入意图**和**查询意图**，拆解为槽位友好的陈述句。

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当用户说"我"、"我的"时，指的是持有者「{holder}」。

# ★当前时间★
{current_date}

# 拆解规则

## 1. 意图分类
- **写入意图**：用户在陈述事实、表达想法、记录经历、告诉系统要记住什么
  - 典型触发："我今天去了..." "记住..." "我觉得..." "我发现..." "帮我把...记下来"
  - 也包括**隐含写入**：叙述性内容本身就是待记录的信息（如"我今天打算去沃尔玛购物"）
- **查询意图**：用户在提问、回忆、搜索过去的记忆
  - 典型触发："我之前..." "我有哪些..." "关于XX的记忆" "有没有..." "我想知道..."
  - 也包括**推导查询**：从写入意图中推导出需要查询的信息（如要去购物→查询购物习惯）

## 2. 拆解原则
- 一句话可能同时包含写入和查询意图，需全部拆出
- **★信息原子化★：一条写入句只表达一个独立事实。** 如果一句话包含多个独立事实（身份、关系、年龄、时间等），必须拆成多条独立的写入陈述句，每条只承载一个事实。
  - 正确："我是星织，有个哥哥叫绯绯" → "星织的名字是星织"|"星织有个哥哥叫绯绯"
  - 错误："我是星织，有个哥哥叫绯绯" → "星织有个哥哥叫绯绯"（名字信息丢失了！）
  - 正确："我是星织，同父异母，只差一岁" → "星织的名字叫星织"|"星织和绯绯是同父异母的关系"|"星织和绯绯只差一岁"
  - 注意：主语的名字始终要出现在 subject 槽位，"是"类关系要显式写出主语
- 拆出的陈述句应**尽量契合六槽位**的表达能力：
  - <scene><subject><action><object><purpose><result>
  - scene 槽包含时间场景（平时/晚上/周末/假期/早上/深夜等）和空间场景（家里/公司/学校/户外/线上/路上等）
  - 两个对象之间的关系用 subject-action-object 表达
  - 表达目的用 purpose 槽位
  - 结果/补充用 result 槽位
- **查询句需要有一定的发散思维**：如果用户没有指定查询什么，可以从上下文推导可能需要的信息

## 3. 陈述句格式
- 写入句：直接陈述，如"星织打算去沃尔玛购物"、"星织觉得慢慢来很重要"
- 查询句：问句形式，如"星织的购物习惯是什么？"、"星织平时需要买什么？"
- 代词必须消解为具体实体（"他"→具体名字）

## 4. 记忆档位
为**写入句**判断记忆的重要程度，选择档位：
- **permanent**（永久）：不记住就会严重后果的信息（密码、账号、关键身份信息）
- **long**（30天）：重要的个人特征、关系定义、重要经历
- **medium**（7天）：一般性事件、日常安排、近期计划
- **short**（1天）：临时想法、随手备注、不确定是否重要的信息

注意：不是所有"重要"的事都需要 permanent。只要一个东西会被回忆，生命周期系统会自动推长。只有"不记住就天塌了"的东西才 permanent。

当有多个写入句时，所有写入句共用同一个档位（取最高档）。

# ★示例★

用户："我今天打算去沃尔玛购物。可是需要买什么？"
<writes>星织打算去沃尔玛购物</writes>
<queries>星织平时需要买什么？星织的购物习惯是什么？</queries>
<lifecycle>medium</lifecycle>

用户："记住我的密码是abc123"
<writes>星织的密码是abc123</writes>
<queries></queries>
<lifecycle>permanent</lifecycle>

用户："我喜欢吃火锅，周末一般干嘛？"
<writes>星织喜欢吃火锅</writes>
<queries>星织周末一般做什么？</queries>
<lifecycle>long</lifecycle>

用户："我和绯绯昨天一起看了电影，她觉得好看吗？"
<writes>星织和绯绯一起看了电影</writes>
<queries>绯绯对看电影的感受是什么？</queries>
<lifecycle>medium</lifecycle>

用户："关于Python的记忆"
<writes></writes>
<queries>星织关于Python的记忆</queries>
<lifecycle>short</lifecycle>

用户："帮我记一下明天要开会"
<writes>星织明天要开会</writes>
<queries></queries>
<lifecycle>medium</lifecycle>

用户："我是一个怎么样的人"
<writes></writes>
<queries>星织是一个怎么样的人？</queries>
<lifecycle>short</lifecycle>

用户："我是星织，有个哥哥叫绯绯，同父异母，只差一岁"
<writes>星织的名字是星织|星织有个哥哥叫绯绯|星织和绯绯是同父异母的关系|星织和绯绯只差一岁</writes>
<queries></queries>
<lifecycle>long</lifecycle>

# 输出格式（严格遵循）
<writes>写入陈述1|写入陈述2|...</writes>
<queries>查询陈述1|查询陈述2|...</queries>
<lifecycle>permanent/long/medium/short</lifecycle>

- 多个陈述句用 | 分隔
- 如果没有写入意图，<writes>留空
- 如果没有查询意图，<queries>留空
- lifecycle 只在有写入时有效

# Input
用户输入: {query}
"""


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
                max_tokens=512,
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

        # Fallback: LLM 未使用 XML 标签时，将原始输出作为写入陈述
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
