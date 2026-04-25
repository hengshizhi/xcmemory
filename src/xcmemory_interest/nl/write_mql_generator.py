# -*- coding: utf-8 -*-
"""
NL → INSERT MQL 生成器

将意图识别产出的写入陈述句翻译为 INSERT MQL 语句。
支持多行生成（多条写入 → 多条 INSERT，分号分隔）。
"""

from __future__ import annotations

import re
from typing import Any


# =============================================================================
# Prompt 模板
# =============================================================================

WRITE_MQL_PROMPT = """# Task
将写入陈述句转换为 INSERT MQL 语句。每个陈述句生成一条 INSERT。

# ★身份声明★
当前记忆系统的持有者是「{holder}」。当陈述句中说"我"时，映射为「{holder}」。

# INSERT 语法
INSERT INTO memories VALUES ('<六槽查询句>', '内容', reference_duration)

- **六槽查询句**格式：`<time><subject><action><object><purpose><result>`
  - time：时间标签。<平时>(永久) | <少年期/童年>(永久) | <那天晚上/深夜/早上>(一天) | <YYYY-MM-DD>
  - subject：执行或承受动作的角色。"我"→'{holder}'
  - action：动作词，从预定义列表选：<是><与><的><同意><拒绝><希望><遵循><发生于><发生><想><说><做>
  - object：action 的承受者
  - purpose：目的/原因/条件。缺槽用 <无>
  - result：结果/补充。缺槽用 <无>
- **内容**：陈述句原文
- **reference_duration**：参考生命周期（秒数），由意图识别给出：{reference_duration}

# 六槽拆解规则
1. 陈述句的核心信息必须分布在六槽中，使 WHERE 条件能精确命中
2. subject 是核心角色，通常是{holder}或用户提到的人
3. action 尽量从预定义列表选，无法匹配时用最接近的单字动词
4. purpose 和 result 是可选的，没有就填 <无>
5. time 根据陈述内容判断：提到具体时间用对应标签，否则填 <无>

# ★示例★
陈述句："星织打算去沃尔玛购物"
→ INSERT INTO memories VALUES ('<无><星织><打算><沃尔玛购物><无><无>', '星织打算去沃尔玛购物', 604800)

陈述句："星织的密码是abc123"
→ INSERT INTO memories VALUES ('<无><星织><是><密码abc123><无><无>', '星织的密码是abc123', 999999)

陈述句："星织喜欢吃火锅"
→ INSERT INTO memories VALUES ('<无><星织><喜欢><火锅><无><无>', '星织喜欢吃火锅', 2592000)

陈述句："星织明天要开会"
→ INSERT INTO memories VALUES ('<明天><星织><做><开会><无><无>', '星织明天要开会', 604800)

# 输出格式（严格遵循，每行一条 INSERT）
<mql>INSERT INTO memories VALUES (...);INSERT INTO memories VALUES (...);...</mql>

- 多条 INSERT 用分号分隔
- 每条 INSERT 独立完整

# Input
写入陈述句（共 {count} 条）：
{statements}
"""


# =============================================================================
# WriteMQLGenerator
# =============================================================================


class WriteMQLGenerator:
    """
    将写入陈述句转换为 INSERT MQL 语句。

    支持：
    - 多条陈述句 → 多条 INSERT（分号分隔）
    - 六槽 query_sentence 生成
    - reference_duration 从意图识别传入
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

    async def generate(
        self,
        statements: list[str],
        reference_duration: int = 86400,
    ) -> dict[str, Any]:
        """
        将写入陈述句列表转换为 INSERT MQL 脚本。

        Args:
            statements: 写入陈述句列表
            reference_duration: 参考生命周期（秒数），默认 86400（1天）

        Returns:
            {
                "mql_script": str,     # 分号分隔的多条 INSERT
                "insert_count": int,   # INSERT 条数
                "raw": str,            # LLM 原始输出
            }
        """
        if not statements:
            return {"mql_script": "", "insert_count": 0, "raw": ""}

        statements_text = "\n".join(f"- {s}" for s in statements)

        prompt = WRITE_MQL_PROMPT.format(
            holder=self.system_holder,
            statements=statements_text,
            count=len(statements),
            reference_duration=reference_duration,
        )

        try:
            resp = await self.llm.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1024,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as e:
            if self.debug:
                print(f"[WriteMQLGenerator DEBUG] LLM error: {e}")
            return {"mql_script": "", "insert_count": 0, "raw": ""}

        if self.debug:
            print(f"[WriteMQLGenerator DEBUG] raw output:\n{raw}\n")

        # 提取 MQL
        mql_text = self._extract_tag(raw, "mql")
        mql_text = mql_text.strip() if mql_text else ""

        # 防御性修复：确保每条都以 INSERT 开头
        if mql_text:
            parts = [p.strip() for p in mql_text.split(";") if p.strip()]
            fixed_parts = []
            for p in parts:
                if not p.upper().startswith("INSERT"):
                    p = "INSERT INTO memories VALUES " + p
                fixed_parts.append(p)
            mql_text = ";".join(fixed_parts)

        insert_count = mql_text.count("INSERT")

        return {
            "mql_script": mql_text,
            "insert_count": insert_count,
            "raw": raw,
        }

    # -------------------------------------------------------------------------
    # 内部方法
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_tag(text: str, tag: str) -> str:
        """从文本中提取 <tag>...</tag> 内容"""
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""
