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

# 六槽定义
格式：`<scene><subject><action><object><purpose><result>`

## 各槽含义
- **scene**：时间/空间场景。永久性事实用 <所有>，具体时间用日期或 <平时>/<少年期> 等，地点用 <家里>/<公司> 等，无特殊场景用 <无>
- **subject**：核心角色。"我"→'{holder}'
- **action**：主体与客体的关系动词。预定义列表：
  <是><有><与><的><叫><差><来自><喜欢><知道><不知道><想><说><做><同意><拒绝><希望><遵循><发生><发生于>
  无法匹配时用最接近的单字动词
- **object**：action 的承受者或关联对象
- **★purpose★**：本条记忆描述的**语义类别**——在回答什么类型的问题？如 <名字>、<身份>、<年龄差距>、<关系>、<喜好>、<经历>、<密码>、<技能>。这是 WHERE 条件的核心命中维度。无明确类别时填 <无>
- **★result★**：上述类别的**具体值或结论**。如 <星织>、<旅行者>、<一岁>、<哥哥>、<火锅>、<abc123>。是对应 purpose 问题的答案。无结论时填 <无>

## 槽位分配核心原则
1. **purpose = 问什么，result = 答什么**
   - 陈述句实质是在回答一个隐式问题：purpose 是问题类型，result 是答案
   - 例："星织的名字是星织" → 问"名字是什么？" → purpose=<名字>, result=<星织>
2. **subject-action-object 构成事实骨架**
   - subject 是被陈述的主体，action 是关系，object 是关联对象
3. **purpose 和 result 拆开写，不要合并到 object**
   - 错误：object=<名字星织>  → 把类别和值混在一起
   - 正确：object=<名字>, purpose=<名字>, result=<星织>
4. **每槽一个独立信息，不堆砌**

# ★★ 范例 ★★

陈述句："星织的名字是星织"
→ INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', {reference_duration})
  解读：主体星织-拥有-名字，这条记忆解答「名字」问题，答案是「星织」

陈述句："星织是不知道自己是谁的旅行者"
→ 拆为两条：
   INSERT INTO memories VALUES ('<无><星织><是><谁><过往><不知道>', '星织不知道自己是谁', {reference_duration})
     解读：主体星织-不知道自己是谁，类别=过往，结论=不知道
   INSERT INTO memories VALUES ('<无><星织><是><旅行者><身份><旅行者>', '星织是旅行者', {reference_duration})
     解读：主体星织-是-旅行者，类别=身份，结论=旅行者

陈述句："星织和绯绯只差一岁"
→ INSERT INTO memories VALUES ('<所有><星织><差><绯绯><年龄差距><一岁>', '星织和绯绯只差一岁', {reference_duration})
  解读：主体星织-差-绯绯，类别=年龄差距，结论=一岁

陈述句："星织有个哥哥叫绯绯"
→ INSERT INTO memories VALUES ('<所有><星织><有><哥哥><关系><绯绯>', '星织有个哥哥叫绯绯', {reference_duration})
  解读：主体星织-有-哥哥，类别=关系，结论=绯绯

陈述句："星织的密码是abc123"
→ INSERT INTO memories VALUES ('<所有><星织><的><密码><密码><abc123>', '星织的密码是abc123', {reference_duration})

陈述句："星织喜欢吃火锅"
→ INSERT INTO memories VALUES ('<平时><星织><喜欢><火锅><喜好><火锅>', '星织喜欢吃火锅', {reference_duration})

陈述句："星织明天要开会"
→ INSERT INTO memories VALUES ('<明天><星织><做><开会><计划><开会>', '星织明天要开会', {reference_duration})

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

        # Fallback: LLM 未用 <mql> 标签时，尝试从原始输出中提取 INSERT 语句
        if not mql_text and raw.strip():
            raw_stripped = raw.strip()
            if "INSERT" in raw_stripped.upper():
                # LLM 直接输出了 INSERT 但没有 XML 标签
                mql_text = raw_stripped
            elif ";" in raw_stripped or "|" in raw_stripped:
                # LLM 输出了分号/竖线分隔的纯文本语句列表，
                # 把它们当作 query_sentence 值，自动补全 INSERT 包装
                sep = ";" if ";" in raw_stripped else "|"
                parts = [p.strip() for p in raw_stripped.split(sep) if p.strip()]
                # 清理掉非陈述文本（如 "long" "short" 等档位词）
                lifecycle_words = {"permanent", "long", "medium", "short"}
                parts = [p for p in parts if p.lower() not in lifecycle_words]
                if parts:
                    mql_text = ";".join(
                        f"INSERT INTO memories VALUES ('<无><{self.system_holder}><是><{p}><无><无>', '{p}', {reference_duration})"
                        for p in parts
                    )
            else:
                # LLM 输出了单条纯文本，当作 content 值
                mql_text = (
                    f"INSERT INTO memories VALUES "
                    f"('<无><{self.system_holder}><是><{raw_stripped}><无><无>', "
                    f"'{raw_stripped}', {reference_duration})"
                )
            if self.debug:
                print(f"[WriteMQLGenerator DEBUG] fallback: using raw output as MQL")

        # 防御性修复：确保每条都以 INSERT 开头
        if mql_text:
            parts = [p.strip() for p in mql_text.split(";") if p.strip()]
            fixed_parts = []
            for p in parts:
                if not p.upper().startswith("INSERT"):
                    p = "INSERT INTO memories VALUES " + p
                fixed_parts.append(p)
            mql_text = ";".join(fixed_parts)

        # 硬兜底：LLM 完全失败时，直接根据 statements 构建 INSERT
        if not mql_text and statements:
            if self.debug:
                print(f"[WriteMQLGenerator DEBUG] hard fallback: building INSERT from statements list")
            parts = []
            for s in statements:
                s_escaped = s.replace("'", "''")
                s_slot_safe = s.replace("<", "＜").replace(">", "＞")
                # 根据陈述内容推断 purpose（简单启发式）
                purpose = self._infer_purpose(s)
                parts.append(
                    f"INSERT INTO memories VALUES "
                    f"('<所有><{self.system_holder}><是><{s_slot_safe}><{purpose}><{s_slot_safe}>', "
                    f"'{s_escaped}', {reference_duration})"
                )
            mql_text = ";".join(parts)

        # ── MQL 质量验证与修正 ──
        if mql_text:
            mql_text = self._validate_and_fix_slots(mql_text)

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

    @staticmethod
    def _infer_purpose(statement: str) -> str:
        """根据陈述内容推断 purpose 类别（简单启发式）"""
        mapping = [
            (["名字", "叫", "称呼"], "名字"),
            (["身份", "是", "角色"], "身份"),
            (["关系", "兄妹", "兄弟", "姐妹", "父", "母", "哥哥", "姐姐", "弟弟", "妹妹"], "关系"),
            (["年龄", "岁", "差距"], "年龄差距"),
            (["喜欢", "爱好", "偏好"], "喜好"),
            (["密码", "账号"], "密码"),
            (["经历", "过去", "曾经"], "经历"),
            (["计划", "打算", "要", "安排"], "计划"),
            (["技能", "会", "擅长"], "技能"),
            (["来自", "出身", "出生"], "来源"),
        ]
        for keywords, purpose in mapping:
            if any(kw in statement for kw in keywords):
                return purpose
        return "事实"

    def _validate_and_fix_slots(self, mql_text: str) -> str:
        """验证并修正 MQL 中的六槽质量问题：短语→词、无无→有值"""
        import re as _re
        # 匹配 VALUES ('<六槽>', 'content', lifecycle)
        pattern = _re.compile(
            r"VALUES\s*\('(<[^>]*>(?:<[^>]*>){5})',\s*'([^']*)',\s*(\d+)\)",
            _re.IGNORECASE,
        )
        fixed_parts = []
        for match in pattern.finditer(mql_text):
            query_sentence = match.group(1)
            content = match.group(2)
            lifecycle = match.group(3)

            # 解析六槽
            slots = _re.findall(r"<([^>]*)>", query_sentence)
            if len(slots) != 6:
                fixed_parts.append(match.group(0))
                continue

            scene, subject, action, obj, purpose, result = slots
            needs_fix = False

            # 检测短句（含空格、句号、逗号或长度>8）
            sentence_chars = _re.compile(r"[，。、\s]")
            for i, slot in enumerate(slots):
                if len(slot) > 8 or sentence_chars.search(slot):
                    needs_fix = True
                    # 尝试取首词
                    shorter = _re.split(r"[，。、\s]+", slot)[0][:8]
                    slots[i] = shorter

            # 检测 purpose + result 双无但有内容
            if purpose == "无" and result == "无" and obj != "无":
                needs_fix = True
                slots[4] = self._infer_purpose(content)  # purpose
                # result 尝试从 obj 或 content 提取关键值
                if obj != "无" and len(obj) <= 4:
                    slots[5] = obj
                elif len(content) <= 6:
                    slots[5] = content

            if needs_fix and self.debug:
                print(f"[WriteMQLGenerator DEBUG] fixed slots: content='{content}' → {slots}")

            if needs_fix:
                # 重建 query_sentence
                new_qs = "".join(f"<{s}>" for s in slots)
                fixed = f"INSERT INTO memories VALUES ('{new_qs}', '{content}', {lifecycle})"
                fixed_parts.append(fixed)
            else:
                fixed_parts.append(match.group(0))

        return ";".join(fixed_parts) if fixed_parts else mql_text
