"""
星尘记忆 - 版本控制数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ChangeType(str, Enum):
    """变更类型枚举"""
    CREATE = "CREATE"       # 创建新记忆
    UPDATE = "UPDATE"       # 更新记忆
    DELETE = "DELETE"      # 删除记忆
    ROLLBACK = "ROLLBACK"   # 回滚操作


@dataclass
class MemoryVersion:
    """
    记忆版本快照

    每次记忆的创建、更新、回滚都会产生一个新版本记录。
    """
    id: str                      # 版本ID: ver_{memory_id}_{version}
    memory_id: str               # 关联的记忆ID
    version: int                 # 版本号（递增）
    query_sentence: str          # 查询句快照
    content: str                 # 记忆内容快照
    lifecycle: int               # 生命周期快照
    created_at: datetime         # 记忆原始创建时间
    updated_at: datetime         # 版本记录时间（每次更新都会变）
    change_type: ChangeType      # 变更类型
    change_summary: str = ""    # 变更摘要
    is_current: bool = False    # 是否当前版本

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "memory_id": self.memory_id,
            "version": self.version,
            "query_sentence": self.query_sentence,
            "content": self.content,
            "lifecycle": self.lifecycle,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "change_type": self.change_type.value,
            "change_summary": self.change_summary,
            "is_current": self.is_current,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryVersion":
        """从字典反序列化"""
        return cls(
            id=d["id"],
            memory_id=d["memory_id"],
            version=d["version"],
            query_sentence=d["query_sentence"],
            content=d.get("content", ""),
            lifecycle=d.get("lifecycle", 0),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            change_type=ChangeType(d["change_type"]),
            change_summary=d.get("change_summary", ""),
            is_current=d.get("is_current", False),
        )

    def to_memory_dict(self) -> dict:
        """
        转换为 VecDBCRUD/Memory 格式的字典

        用于直接用版本数据更新 memories 表
        """
        return {
            "query_sentence": self.query_sentence,
            "content": self.content,
            "lifecycle": self.lifecycle,
        }


@dataclass
class VersionDiff:
    """
    版本差异

    描述两个版本之间的字段变化
    """
    memory_id: str                                    # 记忆ID
    from_version: int                                 # 源版本号
    to_version: int                                   # 目标版本号
    changes: Dict[str, Tuple[Any, Any]] = field(
        default_factory=dict
    )                                                  # 字段变化: {field: (old_value, new_value)}
    summary: str = ""                                  # 差异摘要文本

    def has_changes(self) -> bool:
        """是否有任何变化"""
        return len(self.changes) > 0

    def get_changed_fields(self) -> List[str]:
        """获取有变化的字段列表"""
        return list(self.changes.keys())

    def format_diff(self) -> str:
        """格式化差异为可读文本"""
        if not self.has_changes():
            return "无变化"

        lines = [f"版本 {self.from_version} -> {self.to_version} 差异:"]
        for field, (old_val, new_val) in self.changes.items():
            old_str = f'"{old_val}"' if isinstance(old_val, str) else old_val
            new_str = f'"{new_val}"' if isinstance(new_val, str) else new_val
            lines.append(f"  - {field}: {old_str} -> {new_str}")

        return "\n".join(lines)
