"""
TimeIndex - 时间索引表
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..storage.sql_db import SQLDatabase


class TimeIndex:
    """
    时间索引表

    索引结构：
        1. time_word → set[memory_id]（时间词倒排索引）
        2. created_at → set[memory_id]（时间戳索引，用于范围查询）

    语义映射：
        平时 → 通常时期
        经常 → 高频
        偶尔 → 低频
        最近 → 30天内
        今天 → 当天
    """

    # 时间词到语义的映射
    SEMANTIC_MAP: Dict[str, str] = {
        "平时": "normal",
        "经常": "frequent",
        "有时": "occasional",
        "偶尔": "rare",
        "最近": "recent",
        "今天": "today",
        "昨天": "yesterday",
        "刚才": "just_now",
        "刚刚": "just_now",
    }

    # 语义到时间范围的映射
    SEMANTIC_RANGE: Dict[str, Tuple[Optional[datetime], Optional[datetime]]] = {
        "today": (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
                  datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)),
        "yesterday": (
            (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
            (datetime.now() - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999),
        ),
        "recent": (
            datetime.now() - timedelta(days=30),
            datetime.now(),
        ),
    }

    def __init__(self, sql_db: SQLDatabase):
        """
        初始化时间索引

        Args:
            sql_db: SQL 数据库实例（由 Scheduler 管理）
        """
        self.sql_db = sql_db
        self._init_tables()

    def _init_tables(self):
        """初始化索引表"""
        # 时间词索引表
        self.sql_db.create_table(
            "time_words",
            {
                "memory_id": "TEXT NOT NULL",
                "time_word": "TEXT NOT NULL",
                "created_at": "TEXT NOT NULL",
            },
            if_not_exists=True,
        )

        # 创建索引
        self.sql_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_time_word
            ON time_words(time_word)
        """)
        self.sql_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at
            ON time_words(created_at)
        """)

    def add(self, memory_id: str, time_word: str, created_at: datetime):
        """
        写入时注册时间索引

        Args:
            memory_id: 记忆 ID
            time_word: 时间词，如"平时"、"经常"
            created_at: 创建时间
        """
        self.sql_db.insert(
            "time_words",
            {
                "memory_id": memory_id,
                "time_word": time_word,
                "created_at": created_at.isoformat(),
            },
            or_replace=True,
        )

    def remove(self, memory_id: str):
        """
        删除记忆的时间索引

        Args:
            memory_id: 记忆 ID
        """
        self.sql_db.delete("time_words", {"memory_id": memory_id})

    def query_by_range(
        self,
        start: datetime,
        end: datetime,
    ) -> List[str]:
        """
        按时间范围查询

        Args:
            start: 开始时间
            end: 结束时间

        Returns:
            匹配的 memory_id 列表
        """
        results = self.sql_db.query(
            "SELECT memory_id FROM time_words WHERE created_at >= ? AND created_at <= ? ORDER BY created_at DESC",
            (start.isoformat(), end.isoformat()),
        )
        return [r["memory_id"] for r in results]

    def query_by_words(
        self,
        time_words: List[str],
        fuzzy: bool = True,
    ) -> List[str]:
        """
        按时间词查询

        Args:
            time_words: 时间词列表，如["平时", "经常"]
            fuzzy: 是否启用模糊匹配

        Returns:
            匹配的 memory_id 列表
        """
        if not time_words:
            return []

        if fuzzy:
            # 展开语义映射
            expanded_words = set(time_words)
            for tw in time_words:
                if tw in self.SEMANTIC_MAP:
                    # 添加同一语义的所有时间词
                    semantic = self.SEMANTIC_MAP[tw]
                    for word, sem in self.SEMANTIC_MAP.items():
                        if sem == semantic:
                            expanded_words.add(word)
            time_words = list(expanded_words)

        results = self.sql_db.query(
            f"SELECT DISTINCT memory_id FROM time_words WHERE time_word IN ({','.join(['?' for _ in time_words])})",
            tuple(time_words),
        )
        return [r["memory_id"] for r in results]

    def query_recent(self, days: int = 7) -> List[str]:
        """
        查询最近 N 天的记忆

        Args:
            days: 天数

        Returns:
            匹配的 memory_id 列表
        """
        start = datetime.now() - timedelta(days=days)
        return self.query_by_range(start, datetime.now())

    def query_semantic(self, semantic: str) -> List[str]:
        """
        按语义查询

        Args:
            semantic: 语义词，如 "today", "recent", "frequent"

        Returns:
            匹配的 memory_id 列表
        """
        if semantic not in self.SEMANTIC_RANGE:
            return []

        start, end = self.SEMANTIC_RANGE[semantic]
        if start is None:
            start = datetime.min
        if end is None:
            end = datetime.max

        return self.query_by_range(start, end)

    def get_time_words(self, memory_id: str) -> List[str]:
        """
        获取记忆对应的时间词列表

        Args:
            memory_id: 记忆 ID

        Returns:
            时间词列表
        """
        results = self.sql_db.select(
            "time_words",
            columns=["time_word"],
            where={"memory_id": memory_id},
        )
        return [r["time_word"] for r in results]

    def get_created_at(self, memory_id: str) -> Optional[datetime]:
        """
        获取记忆的创建时间

        Args:
            memory_id: 记忆 ID

        Returns:
            创建时间，不存在返回 None
        """
        results = self.sql_db.select(
            "time_words",
            columns=["created_at"],
            where={"memory_id": memory_id},
            limit=1,
        )
        if not results:
            return None
        return datetime.fromisoformat(results[0]["created_at"])

    def count_by_word(self) -> Dict[str, int]:
        """
        统计每个时间词的记忆数量

        Returns:
            {time_word: count}
        """
        results = self.sql_db.query(
            "SELECT time_word, COUNT(*) as cnt FROM time_words GROUP BY time_word"
        )
        return {r["time_word"]: r["cnt"] for r in results}

    def clear(self):
        """清空所有时间索引"""
        self.sql_db.clear("time_words")
