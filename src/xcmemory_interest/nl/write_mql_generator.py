# -*- coding: utf-8 -*-
"""
NL → INSERT MQL 生成器

将意图识别产出的写入陈述句翻译为 INSERT MQL 语句。
支持多行生成（多条写入 → 多条 INSERT，分号分隔）。
"""

from __future__ import annotations

import re
from typing import Any


from ..prompts.nl import WRITE_MQL_PROMPT


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
                # match.group(0) 只匹配 VALUES (...) 需要补回 INSERT INTO memories 前缀
                fixed_parts.append(f"INSERT INTO memories {match.group(0)}")

        return ";".join(fixed_parts) if fixed_parts else mql_text
