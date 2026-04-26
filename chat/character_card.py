# -*- coding: utf-8 -*-
"""
角色卡加载与解析

角色卡是 YAML 文件，定义角色的身份、意识设定、性格等。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CharacterCard:
    """角色卡"""

    name: str
    avatar: str = "✨"
    system_name: str = "default"       # 记忆系统名称
    introduction: str = ""
    consciousness: str = ""
    personality_tags: list[str] = field(default_factory=list)
    dialogue_style: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "CharacterCard":
        """
        从 YAML 文件加载角色卡。

        Args:
            path: YAML 文件路径

        Returns:
            CharacterCard 实例
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"角色卡文件不存在: {p}")

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "name" not in data:
            raise ValueError(f"角色卡缺少 name 字段: {p}")

        return cls(
            name=data["name"],
            avatar=data.get("avatar", "✨"),
            system_name=data.get("system_name", "default"),
            introduction=data.get("introduction", ""),
            consciousness=data.get("consciousness", ""),
            personality_tags=data.get("personality_tags", []),
            dialogue_style=data.get("dialogue_style", {}),
        )

    def get_holder(self) -> str:
        """获取用于记忆系统的 holder 名称（即角色名）"""
        return self.name

    def get_system_prompt_section(self) -> str:
        """生成注入 system prompt 的角色信息"""
        parts = []

        if self.introduction:
            parts.append(f"## 你的身份\n{self.introduction}")

        if self.consciousness:
            parts.append(f"## 意识设定与人格\n{self.consciousness}")

        if self.dialogue_style:
            tone = self.dialogue_style.get("tone", "")
            habits = self.dialogue_style.get("habits", [])
            style_parts = []
            if tone:
                style_parts.append(f"语气：{tone}")
            if habits:
                style_parts.append("习惯：\n" + "\n".join(f"- {h}" for h in habits))
            if style_parts:
                parts.append(f"## 对话风格\n" + "\n".join(style_parts))

        return "\n\n".join(parts)
