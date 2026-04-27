"""
NL → 6槽记忆提取器 (Slot Extractor)

从自然语言文本中提取记忆的6槽结构，输出严格遵循 MQL 书写规范。
"""

import re
import json
from openai import AsyncClient


# 预定义场景词（时间场景 + 空间场景）
PREDEFINED_SCENE_WORDS = [
    # 时间场景
    "平时", "少年期", "童年", "那天晚上", "深夜", "早上", "晚上", "白天",
    "周末", "假期", "本周早些时候",
    # 空间场景
    "家里", "公司", "学校", "户外", "线上", "路上",
]

# 预定义动词
PREDEFINED_ACTIONS = [
    "是", "有", "与", "的", "叫", "差", "来自", "喜欢", "知道", "不知道",
    "同意", "拒绝", "希望", "遵循", "发生于", "发生", "想", "说", "做"
]

# lifecycle 映射（scene 词 → 基础 lifecycle；意图识别可覆盖）
LIFECYCLE_MAP = {
    # 时间场景 - 永久
    "平时": 999999,
    "少年期": 999999,
    "童年": 999999,
    # 时间场景 - 一周
    "本周早些时候": 604800,
    "周末": 604800,
    "假期": 604800,
    # 时间场景 - 一天
    "那天晚上": 86400,
    "深夜": 86400,
    "早上": 86400,
    "晚上": 86400,
    "白天": 86400,
    # 空间场景 - 默认一天（由意图识别根据内容调整）
    "家里": 86400,
    "公司": 86400,
    "学校": 86400,
    "户外": 86400,
    "线上": 86400,
    "路上": 86400,
}

# 默认 lifecycle
DEFAULT_LIFECYCLE = 86400


NL_TO_SLOTS_PROMPT = """
# Task Objective
从自然语言文本中提取记忆的 6 槽结构，输出严格遵循 MQL 书写规范。

# 6 槽定义
格式：`<scene><subject><action><object><purpose><result>`

- **scene**：时间或空间场景。只用一个预定义词：
  时间：<平时>/<少年期>/<童年>/<那天晚上>/<深夜>/<早上>/<晚上>/<白天>/<周末>/<假期>/<本周早些时候>/<YYYY-MM-DD>
  空间：<家里>/<公司>/<学校>/<户外>/<线上>/<路上>
- **subject**：核心角色，被陈述的主体。未指明时默认"我"
- **action**：主体与客体的关系。严格用预定义词：<是>/<有>/<与>/<的>/<叫>/<差>/<来自>/<喜欢>/<知道>/<不知道>/<同意>/<拒绝>/<希望>/<遵循>/<发生于>/<发生>/<想>/<说>/<做>。无法匹配时用最接近的单字
- **object**：action 的承受者或关联对象
- **★purpose**：本条记忆描述的**语义类别**——在回答什么类型的问题？如 <名字> <身份> <年龄差距> <关系> <喜好> <经历> <密码> <技能> <过往> <计划>。这是查询命中的核心维度
- **★result**：上述类别的**具体值或结论**。如 <星织> <旅行者> <一岁> <哥哥> <火锅> <abc123> <不知道> <开会>

# 规则
- **★每个槽位只写一个词，不写短句★**（核心规则）
  错误：object=<被父亲交给哥哥照顾>  → 这是短句
  正确：应拆为 action=<做>, object=<照顾>, purpose=<照顾者>, result=<哥哥>
  错误：object=<发展恋人关系> → 这是短语
  正确：action=<希望>, object=<恋人>, purpose=<关系>, result=<发展>
  槽位是关键词索引，不是叙述文本。多词信息应拆分到不同槽位
- 六槽必须等长，缺槽用 <无> 占位
- 只提取明确提到的信息，不过度推断
- 单一事实：一条记忆只表达一个独立事实
- purpose 和 result 拆开写，不要合并到 object 中
- description 不重复六槽已有信息
- scene 槽必须是单一场景词，不能塞入其他信息
- 当文本同时暗示时间和空间时，优先选最突出的那个场景维度填入 scene 槽

# 六槽填充示例（每个槽位一个词，不写短句）
1. "星织是女性"
   - scene=<平时>, subject=<星织>, action=<是>, object=<女性>, purpose=<性别>, result=<女性>

2. "星织的名字是星织"
   - scene=<所有>, subject=<星织>, action=<的>, object=<名字>, purpose=<名字>, result=<星织>

3. "星织有个哥哥叫绯绯"
   - scene=<所有>, subject=<星织>, action=<有>, object=<哥哥>, purpose=<关系>, result=<绯绯>

4. "星织和绯绯只差一岁"
   - scene=<所有>, subject=<星织>, action=<差>, object=<绯绯>, purpose=<年龄差距>, result=<一岁>

5. "星织是旅行者"
   - scene=<平时>, subject=<星织>, action=<是>, object=<旅行者>, purpose=<身份>, result=<旅行者>

6. "星织不知道自己是谁"
   - scene=<平时>, subject=<星织>, action=<不知道>, object=<谁>, purpose=<过往>, result=<不知道>

7. "绯绯希望星织发展成恋人关系"
   - scene=<平时>, subject=<绯绯>, action=<希望>, object=<星织>, purpose=<关系>, result=<恋人>

8. "星织同意与绯绯发展关系，但要求慢慢来"
   - scene=<深夜>, subject=<星织>, action=<同意>, object=<绯绯>, purpose=<节奏>, result=<慢慢来>

9. "早上哥哥醒来，星织还在睡"
   - scene=<早上>, subject=<哥哥>, action=<醒来>, object=<无>, purpose=<无>, result=<无>

10. "我在家里喜欢看书"
    - scene=<家里>, subject=<我>, action=<喜欢>, object=<读书>, purpose=<喜好>, result=<读书>

11. "周末去户外骑自行车"
    - scene=<周末>, subject=<我>, action=<做>, object=<骑自行车>, purpose=<活动>, result=<户外>

12. "星织的密码是abc123"
    - scene=<所有>, subject=<星织>, action=<的>, object=<密码>, purpose=<密码>, result=<abc123>

# 输出格式
<slots>
{{"scene": "", "subject": "", "action": "", "object": "", "purpose": "", "result": ""}}
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
        #         "scene": "<深夜>",
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
            - slots: 6槽字典，键为 scene/subject/action/object/purpose/result
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
                "scene": "",
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
            "scene": "",
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

        # 从 scene 槽推断
        scene_value = slots.get("scene", "").strip("<>")
        if scene_value in LIFECYCLE_MAP:
            return LIFECYCLE_MAP[scene_value]

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
        for key in ["scene", "subject", "action", "object", "purpose", "result"]:
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
        required_keys = ["scene", "subject", "action", "object", "purpose", "result"]
        for key in required_keys:
            if key not in slots:
                return False, f"缺少必需槽位: {key}"

        scene_value = slots.get("scene", "").strip("<>")
        if scene_value and not SlotValidator.validate_scene(f"<{scene_value}>"):
            return False, f"非法的场景词: {scene_value}"

        action_value = slots.get("action", "").strip("<>")
        if action_value and not SlotValidator.validate_action(f"<{action_value}>"):
            # 允许其他动词，但记录警告
            pass

        return True, ""

    @staticmethod
    def validate_scene(scene_str: str) -> bool:
        """
        验证场景词是否合法

        Args:
            scene_str: 场景字符串，例如 "<平时>" 或 "平时"

        Returns:
            是否合法
        """
        value = scene_str.strip("<>")
        return value in PREDEFINED_SCENE_WORDS or re.match(r"^\d{4}-\d{2}-\d{2}$", value) is not None

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
        f"{slots.get('scene', '<无>')}"
        f"{slots.get('subject', '<无>')}"
        f"{slots.get('action', '<无>')}"
        f"{slots.get('object', '<无>')}"
        f"{slots.get('purpose', '<无>')}"
        f"{slots.get('result', '<无>')}"
    )
