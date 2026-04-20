"""
NL → 6槽记忆提取器 (Slot Extractor)

从自然语言文本中提取记忆的6槽结构，输出严格遵循 MQL 书写规范。
"""

import re
import json
from openai import AsyncClient


# 预定义时间词
PREDEFINED_TIME_WORDS = [
    "平时", "少年期", "童年", "那天晚上", "深夜", "早上", "本周早些时候"
]

# 预定义动词
PREDEFINED_ACTIONS = [
    "是", "与", "的", "同意", "拒绝", "希望", "遵循",
    "发生于", "发生", "想", "说", "做"
]

# lifecycle 映射
LIFECYCLE_MAP = {
    "平时": 999999,
    "少年期": 999999,
    "童年": 999999,
    "本周早些时候": 604800,
    "那天晚上": 86400,
    "深夜": 86400,
    "早上": 86400,
}

# 默认 lifecycle
DEFAULT_LIFECYCLE = 86400


NL_TO_SLOTS_PROMPT = """
# Task Objective
从自然语言文本中提取记忆的 6 槽结构，输出严格遵循 MQL 书写规范。

# 6 槽定义
- subject: 主体 - 谁是主角？
- time: 时间 - 只用预定义时间词之一：<平时>/<少年期>/<童年>/<那天晚上>/<深夜>/<早上>/<本周早些时候>/<YYYY-MM-DD>
- action: 动作 - 严格使用预定义动词：<是>/<与>/<的>/<同意>/<拒绝>/<希望>/<遵循>/<发生于>/<发生>/<想>/<说>/<做>
- object: 宾语 - action 的直接承受者
- purpose: 目的/原因/条件
- result: action 的结果或最终状态

# 规则
- 六槽必须等长，缺槽用 <无> 占位
- 只提取明确提到的信息，不过度推断
- subject 默认为"我"如果未指明
- lifecycle 推断：<平时>/<少年期>/<童年> → 999999；<本周早些时候> → 604800；<那天晚上>/<深夜>/<早上> → 86400
- 单一事实：一条记忆只表达一个独立事实
- description 不重复六槽已有信息
- time 槽必须是单一时间词，不能塞入其他信息

# 六槽填充示例
1. "星织是女性"
   - time=<平时>, subject=<星织>, action=<是>, object=<女性>, purpose=<无>, result=<无>
   - description: "星织是女性"

2. "绯绯希望星织发展成恋人关系"
   - time=<平时>, subject=<绯绯>, action=<希望>, object=<星织>, purpose=<发展>, result=<恋人>
   - description: "绯绯希望星织发展成恋人关系"

3. "星织同意与绯绯发展恋人关系，但要求慢慢来"
   - time=<深夜>, subject=<星织>, action=<同意>, object=<绯绯>, purpose=<发展恋人关系>, result=<慢慢来>
   - description: "星织同意与绯绯发展恋人关系，但要求慢慢来"

4. "早上哥哥醒来，星织还在睡"
   - time=<早上>, subject=<哥哥>, action=<醒来>, object=<星织还在睡>, purpose=<无>, result=<无>
   - description: "早上哥哥醒来时星织还在睡"

# 输出格式
<slots>
{{"time": "", "subject": "", "action": "", "object": "", "purpose": "", "result": ""}}
</slots>

<description>
整理后的记忆内容摘要（不重复六槽已有信息）
</description>

<lifecycle>
推断的 lifecycle 数值（999999/604800/86400）
</lifecycle>

# Input
自然语言文本: {nl_text}
"""


class SlotExtractor:
    """
    从自然语言文本中提取记忆的6槽结构

    使用示例:
        from openai import AsyncClient
        from xcmemory_interest.nl import SlotExtractor

        client = AsyncClient(api_key="your-api-key")
        extractor = SlotExtractor(client)

        result = await extractor.extract("星织同意与绯绯发展恋人关系，但要求慢慢来")
        # result = {
        #     "slots": {
        #         "time": "<深夜>",
        #         "subject": "<星织>",
        #         "action": "<同意>",
        #         "object": "<绯绯>",
        #         "purpose": "<发展恋人关系>",
        #         "result": "<慢慢来>"
        #     },
        #     "description": "星织同意与绯绯发展恋人关系，但要求慢慢来",
        #     "lifecycle": 999999
        # }
    """

    def __init__(self, llm_client: AsyncClient):
        """
        初始化 SlotExtractor

        Args:
            llm_client: OpenAI AsyncClient 实例，与 mql_generator.py 中的接口保持一致
        """
        self.llm = llm_client

    async def extract(self, nl_text: str) -> dict:
        """
        从自然语言文本中提取6槽记忆结构

        Args:
            nl_text: 自然语言输入文本

        Returns:
            包含以下键的字典:
            - slots: 6槽字典，键为 time/subject/action/object/purpose/result
            - description: 整理后的记忆内容摘要
            - lifecycle: 推断的 lifecycle 数值
        """
        prompt = NL_TO_SLOTS_PROMPT.format(nl_text=nl_text)

        response = await self.llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )

        response_text = response.choices[0].message.content

        slots = self._extract_slots(response_text)
        description = self._extract_tag(response_text, "description")
        lifecycle = self._extract_lifecycle(response_text, slots)

        # 确保 slots 值用 <> 包裹
        slots = self._normalize_slots(slots)

        return {
            "slots": slots,
            "description": description,
            "lifecycle": lifecycle,
        }

    def _extract_slots(self, text: str) -> dict:
        """
        从响应文本中提取 slots JSON

        Args:
            text: LLM 响应文本

        Returns:
            slots 字典
        """
        slots_str = self._extract_tag(text, "slots")
        if not slots_str:
            return {
                "time": "",
                "subject": "",
                "action": "",
                "object": "",
                "purpose": "",
                "result": "",
            }

        try:
            # 尝试解析 JSON
            slots = json.loads(slots_str)
            if isinstance(slots, dict):
                return slots
        except json.JSONDecodeError:
            pass

        # 如果 JSON 解析失败，尝试从文本中提取
        return self._parse_slots_from_text(slots_str)

    def _parse_slots_from_text(self, text: str) -> dict:
        """
        从非标准格式的文本中解析 slots

        Args:
            text: 可能包含 slots 信息的文本

        Returns:
            解析后的 slots 字典
        """
        result = {
            "time": "",
            "subject": "",
            "action": "",
            "object": "",
            "purpose": "",
            "result": "",
        }

        # 尝试匹配 key: value 格式
        for key in result.keys():
            # 匹配 "key": "value" 或 'key': 'value' 格式
            patterns = [
                rf'"{key}"\s*:\s*"([^"]*)"',
                rf"'{key}'\s*:\s*'([^']*)'",
                rf'"{key}"\s*:\s*<([^>]*)>',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    result[key] = match.group(1).strip()
                    break

        return result

    def _extract_tag(self, text: str, tag: str) -> str:
        """
        从文本中提取指定标签的内容

        Args:
            text: 原始文本
            tag: 标签名

        Returns:
            标签内的内容，如果未找到则返回空字符串
        """
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _extract_lifecycle(self, text: str, slots: dict) -> int:
        """
        从响应文本或 slots 中推断 lifecycle

        Args:
            text: LLM 响应文本
            slots: 已解析的 slots 字典

        Returns:
            lifecycle 数值
        """
        # 首先尝试从文本中提取
        lifecycle_str = self._extract_tag(text, "lifecycle")
        if lifecycle_str:
            try:
                return int(lifecycle_str.strip())
            except ValueError:
                pass

        # 从 time 槽推断
        time_value = slots.get("time", "").strip("<>")
        if time_value in LIFECYCLE_MAP:
            return LIFECYCLE_MAP[time_value]

        return DEFAULT_LIFECYCLE

    def _normalize_slots(self, slots: dict) -> dict:
        """
        确保 slots 值用 <> 包裹，并填充默认值

        Args:
            slots: 原始 slots 字典

        Returns:
            规范化后的 slots 字典
        """
        normalized = {}
        for key, value in slots.items():
            value = value.strip() if value else ""
            # 如果值非空且不以 <> 包裹，则加上
            if value and not value.startswith("<"):
                value = f"<{value}>"
            if value and not value.endswith(">"):
                value = f"{value}>"
            normalized[key] = value

        # 填充缺失的槽为 <无>
        for key in ["time", "subject", "action", "object", "purpose", "result"]:
            if key not in normalized or not normalized[key]:
                normalized[key] = "<无>"

        # subject 默认值为 "我"
        if normalized.get("subject") == "<无>" or not normalized.get("subject"):
            normalized["subject"] = "<我>"

        return normalized


class SlotValidator:
    """
    验证 slots 结构的合法性
    """

    @staticmethod
    def validate(slots: dict) -> tuple[bool, str]:
        """
        验证 slots 是否符合规范

        Args:
            slots: 6槽字典

        Returns:
            (是否合法, 错误信息)
        """
        required_keys = ["time", "subject", "action", "object", "purpose", "result"]
        for key in required_keys:
            if key not in slots:
                return False, f"缺少必需槽位: {key}"

        time_value = slots.get("time", "").strip("<>")
        if time_value and not SlotValidator.validate_time(f"<{time_value}>"):
            return False, f"非法的时间词: {time_value}"

        action_value = slots.get("action", "").strip("<>")
        if action_value and not SlotValidator.validate_action(f"<{action_value}>"):
            # 允许其他动词，但记录警告
            pass

        return True, ""

    @staticmethod
    def validate_time(time_str: str) -> bool:
        """
        验证时间词是否合法

        Args:
            time_str: 时间字符串，例如 "<平时>" 或 "平时"

        Returns:
            是否合法
        """
        value = time_str.strip("<>")
        return value in PREDEFINED_TIME_WORDS or re.match(r"^\d{4}-\d{2}-\d{2}$", value) is not None

    @staticmethod
    def validate_action(action_str: str) -> bool:
        """
        验证动作词是否合法

        Args:
            action_str: 动作字符串，例如 "<是>" 或 "是"

        Returns:
            是否合法
        """
        value = action_str.strip("<>")
        return value in PREDEFINED_ACTIONS


def slots_to_query_sentence(slots: dict) -> str:
    """
    将 slots 字典转换为 query_sentence 字符串

    Args:
        slots: 6槽字典

    Returns:
        合并后的 query_sentence 字符串
    """
    return (
        f"{slots.get('time', '<无>')}"
        f"{slots.get('subject', '<无>')}"
        f"{slots.get('action', '<无>')}"
        f"{slots.get('object', '<无>')}"
        f"{slots.get('purpose', '<无>')}"
        f"{slots.get('result', '<无>')}"
    )
