"""
星尘记忆 - 版本管理器核心实现

提供单记忆级别的版本控制能力：
- 版本历史记录（支持存档阈值策略）
- 版本查询
- 版本回滚
- 版本对比
- 手动存档
"""

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING

from .models import MemoryVersion, VersionDiff, ChangeType

if TYPE_CHECKING:
    from ..basic_crud.vec_db_crud import VecDBCRUD, Memory


class VersionManager:
    """
    单记忆版本控制管理器

    负责：
    1. 记录记忆的每次变更（创建/更新/删除）- 遵循存档阈值策略
    2. 提供版本历史查询
    3. 提供版本回滚能力
    4. 提供版本对比功能
    5. 提供手动存档功能

    存档策略：
    - 默认每 10 条记忆存档一次版本（可配置）
    - 设置 record_version=False 可跳过单次记录
    - 手动调用 archive() 可强制存档

    数据存储：
    - 版本数据存储在 SQLite 的 `memory_versions` 表中
    - 与 VecDBCRUD 共用同一个数据库连接
    """

    def __init__(
        self,
        vec_db: "VecDBCRUD",
        archive_threshold: int = 10,  # 每 N 条记忆存档一次
        max_versions_per_memory: int = 50,  # 每条记忆最多保留版本数
    ):
        """
        初始化版本管理器

        Args:
            vec_db: VecDBCRUD 实例（共享同一个 SQLite 连接）
            archive_threshold: 存档阈值，每 N 条记忆存档一次版本
            max_versions_per_memory: 每条记忆最多保留的版本数
        """
        self._vec_db = vec_db
        self._archive_threshold = archive_threshold
        self._max_versions = max_versions_per_memory
        self._init_version_table()

    @property
    def archive_threshold(self) -> int:
        """获取存档阈值"""
        return self._archive_threshold

    @archive_threshold.setter
    def archive_threshold(self, value: int):
        """设置存档阈值"""
        if value < 1:
            raise ValueError("archive_threshold must be >= 1")
        self._archive_threshold = value

    def _init_version_table(self):
        """初始化版本表"""
        conn = self._vec_db._conn
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS memory_versions (
                id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                query_sentence TEXT NOT NULL,
                content TEXT,
                lifecycle INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                change_type TEXT NOT NULL,
                change_summary TEXT,
                is_current INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                update_sequence INTEGER DEFAULT 0,
                UNIQUE(memory_id, version)
            )
        """)

        # 创建索引加速查询
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_versions_memory
            ON memory_versions(memory_id, version DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_versions_current
            ON memory_versions(memory_id, is_current)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_versions_archived
            ON memory_versions(memory_id, is_archived)
        """)

        conn.commit()

    def _generate_version_id(self, memory_id: str, version: int) -> str:
        """生成版本ID"""
        return f"ver_{memory_id}_{version}"

    def _get_next_version(self, memory_id: str) -> int:
        """获取下一个版本号"""
        cur = self._vec_db._conn.cursor()
        cur.execute(
            "SELECT MAX(version) FROM memory_versions WHERE memory_id = ?",
            (memory_id,)
        )
        row = cur.fetchone()
        max_ver = row[0]
        return (max_ver or 0) + 1

    def _get_next_sequence(self, memory_id: str) -> int:
        """获取下一个更新序列号"""
        cur = self._vec_db._conn.cursor()
        cur.execute(
            "SELECT MAX(update_sequence) FROM memory_versions WHERE memory_id = ?",
            (memory_id,)
        )
        row = cur.fetchone()
        max_seq = row[0]
        return (max_seq or 0) + 1

    def _should_archive(self, memory_id: str) -> bool:
        """
        判断是否应该存档

        根据 update_sequence 判断是否达到存档阈值
        """
        sequence = self._get_next_sequence(memory_id)
        # 当序列号是存档阈值的倍数时存档
        return sequence > 0 and sequence % self._archive_threshold == 0

    def _get_unarchived_versions(self, memory_id: str) -> List[int]:
        """获取未存档的版本号列表"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute("""
            SELECT version FROM memory_versions
            WHERE memory_id = ? AND is_archived = 0
            ORDER BY version ASC
        """, (memory_id,))
        return [row[0] for row in cur.fetchall()]

    # =========================================================================
    # 写入
    # =========================================================================

    def record_create(
        self,
        memory_id: str,
        memory: "Memory",
        force_archive: bool = False,
    ) -> Optional[str]:
        """
        记录记忆创建

        Args:
            memory_id: 记忆ID
            memory: Memory 对象
            force_archive: 是否强制存档（创建时默认存档）

        Returns:
            版本ID，如果未达到存档条件则返回 None
        """
        version = 1
        version_id = self._generate_version_id(memory_id, version)
        now = datetime.now().isoformat()
        created_at = memory.created_at.isoformat() if memory.created_at else now

        conn = self._vec_db._conn
        conn.cursor().execute("""
            INSERT INTO memory_versions (
                id, memory_id, version, query_sentence, content, lifecycle,
                created_at, updated_at, change_type, change_summary, is_current,
                is_archived, update_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id,
            memory_id,
            version,
            memory.query_sentence,
            memory.content,
            memory.lifecycle,
            created_at,
            now,
            ChangeType.CREATE.value,
            "记忆创建",
            1,  # is_current
            1,  # is_archived（创建版本默认存档）
            1,  # update_sequence
        ))
        conn.commit()

        return version_id

    def record_update(
        self,
        memory_id: str,
        old_memory: "Memory",
        new_memory: "Memory",
        force_archive: bool = False,
    ) -> Optional[str]:
        """
        记录记忆更新

        Args:
            memory_id: 记忆ID
            old_memory: 更新前的 Memory 对象
            new_memory: 更新后的 Memory 对象
            force_archive: 是否强制存档

        Returns:
            版本ID，如果未达到存档条件则返回 None（除非 force_archive=True）
        """
        # 1. 获取下一个版本号和序列号
        version = self._get_next_version(memory_id)
        sequence = self._get_next_sequence(memory_id)
        version_id = self._generate_version_id(memory_id, version)
        now = datetime.now().isoformat()

        # 2. 生成变更摘要
        change_summary = self._generate_change_summary(old_memory, new_memory)

        # 3. 检查是否应该存档
        should_archive = force_archive or (sequence > 0 and sequence % self._archive_threshold == 0)

        if not should_archive:
            # 不存档：更新序列号，但不创建新版本
            # 将更新合并到当前存档版本
            conn = self._vec_db._conn
            conn.cursor().execute("""
                UPDATE memory_versions
                SET content = ?,
                    lifecycle = ?,
                    updated_at = ?,
                    change_summary = ?,
                    update_sequence = ?
                WHERE memory_id = ? AND is_current = 1 AND is_archived = 1
            """, (
                new_memory.content,
                new_memory.lifecycle,
                now,
                change_summary,
                sequence,
                memory_id,
            ))
            conn.commit()
            return None  # 未存档，返回 None

        # 4. 存档：将旧版本标记为非当前
        conn = self._vec_db._conn
        conn.cursor().execute("""
            UPDATE memory_versions
            SET is_current = 0
            WHERE memory_id = ? AND is_current = 1
        """, (memory_id,))

        # 5. 插入新版本
        conn.cursor().execute("""
            INSERT INTO memory_versions (
                id, memory_id, version, query_sentence, content, lifecycle,
                created_at, updated_at, change_type, change_summary, is_current,
                is_archived, update_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id,
            memory_id,
            version,
            new_memory.query_sentence,
            new_memory.content,
            new_memory.lifecycle,
            old_memory.created_at.isoformat(),  # 保持原始创建时间
            now,
            ChangeType.UPDATE.value,
            change_summary,
            1,  # is_current
            1,  # is_archived
            sequence,
        ))
        conn.commit()

        # 6. 自动清理旧版本（如果超过最大保留数）
        self._auto_prune(memory_id)

        return version_id

    def record_delete(self, memory_id: str) -> str:
        """
        记录记忆删除（软删除版本）

        Args:
            memory_id: 记忆ID

        Returns:
            版本ID
        """
        # 读取当前记忆
        memory = self._vec_db._kv_read(memory_id)
        if memory is None:
            return None

        version = self._get_next_version(memory_id)
        version_id = self._generate_version_id(memory_id, version)
        now = datetime.now().isoformat()

        conn = self._vec_db._conn

        # 标记旧版本为非当前
        conn.cursor().execute("""
            UPDATE memory_versions
            SET is_current = 0
            WHERE memory_id = ? AND is_current = 1
        """, (memory_id,))

        # 插入删除版本记录
        conn.cursor().execute("""
            INSERT INTO memory_versions (
                id, memory_id, version, query_sentence, content, lifecycle,
                created_at, updated_at, change_type, change_summary, is_current,
                is_archived, update_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id,
            memory_id,
            version,
            memory.query_sentence,
            memory.content,
            memory.lifecycle,
            memory.created_at.isoformat(),
            now,
            ChangeType.DELETE.value,
            "记忆删除",
            1,  # is_current
            1,  # is_archived
            0,  # update_sequence
        ))
        conn.commit()

        return version_id

    def _generate_change_summary(
        self,
        old_memory: "Memory",
        new_memory: "Memory",
    ) -> str:
        """自动生成变更摘要"""
        changes = []

        if old_memory.content != new_memory.content:
            old_content = old_memory.content[:20] + "..." if len(old_memory.content) > 20 else old_memory.content
            new_content = new_memory.content[:20] + "..." if len(new_memory.content) > 20 else new_memory.content
            changes.append(f"content: {old_content} -> {new_content}")

        if old_memory.lifecycle != new_memory.lifecycle:
            changes.append(f"lifecycle: {old_memory.lifecycle} -> {new_memory.lifecycle}")

        return "; ".join(changes) if changes else "无变化"

    # =========================================================================
    # 手动存档
    # =========================================================================

    def archive(self, memory_id: str) -> Optional[str]:
        """
        手动存档当前版本

        强制将未存档的更新合并到存档版本中，创建一个新的存档快照。

        Args:
            memory_id: 记忆ID

        Returns:
            新存档版本ID，如果失败返回 None
        """
        # 读取当前记忆
        memory = self._vec_db._kv_read(memory_id)
        if memory is None:
            return None

        # 读取当前存档版本
        current = self.get_version(memory_id)
        if current is None:
            return self.record_create(memory_id, memory, force_archive=True)

        # 创建新的存档版本
        version = self._get_next_version(memory_id)
        sequence = self._get_next_sequence(memory_id)
        version_id = self._generate_version_id(memory_id, version)
        now = datetime.now().isoformat()

        conn = self._vec_db._conn

        # 标记旧版本为非当前
        conn.cursor().execute("""
            UPDATE memory_versions
            SET is_current = 0
            WHERE memory_id = ? AND is_current = 1
        """, (memory_id,))

        # 插入新存档版本
        conn.cursor().execute("""
            INSERT INTO memory_versions (
                id, memory_id, version, query_sentence, content, lifecycle,
                created_at, updated_at, change_type, change_summary, is_current,
                is_archived, update_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id,
            memory_id,
            version,
            memory.query_sentence,
            memory.content,
            memory.lifecycle,
            memory.created_at.isoformat(),
            now,
            ChangeType.UPDATE.value,
            "手动存档",
            1,
            1,  # is_archived
            sequence,
        ))
        conn.commit()

        return version_id

    def archive_all(self) -> Dict[str, str]:
        """
        对所有有未存档更新的记忆执行存档

        Returns:
            {memory_id: version_id} 字典
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        # 找出所有有未存档版本的记忆
        cur.execute("""
            SELECT DISTINCT memory_id FROM memory_versions
            WHERE is_archived = 0 AND change_type != 'DELETE'
        """)
        memory_ids = [row[0] for row in cur.fetchall()]

        results = {}
        for memory_id in memory_ids:
            vid = self.archive(memory_id)
            if vid:
                results[memory_id] = vid

        return results

    # =========================================================================
    # 查询
    # =========================================================================

    def get_version_history(
        self,
        memory_id: str,
        limit: int = 10,
        include_deleted: bool = True,
        archived_only: bool = False,
    ) -> List[MemoryVersion]:
        """
        获取记忆的版本历史

        Args:
            memory_id: 记忆ID
            limit: 返回数量限制
            include_deleted: 是否包含已删除版本
            archived_only: 是否只返回存档版本

        Returns:
            MemoryVersion 列表（按版本号降序）
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        sql = """
            SELECT * FROM memory_versions
            WHERE memory_id = ?
        """
        params = [memory_id]

        if archived_only:
            sql += " AND is_archived = 1"

        if not include_deleted:
            sql += " AND change_type != ?"
            params.append(ChangeType.DELETE.value)

        sql += " ORDER BY version DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, params)
        rows = cur.fetchall()
        return [self._row_to_version(row) for row in rows]

    def get_version(
        self,
        memory_id: str,
        version: int = None,
    ) -> Optional[MemoryVersion]:
        """
        获取指定版本

        Args:
            memory_id: 记忆ID
            version: 版本号（None=获取当前版本）

        Returns:
            MemoryVersion 或 None
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        if version is None:
            # 获取当前版本
            cur.execute(
                "SELECT * FROM memory_versions WHERE memory_id = ? AND is_current = 1",
                (memory_id,)
            )
        else:
            cur.execute(
                "SELECT * FROM memory_versions WHERE memory_id = ? AND version = ?",
                (memory_id, version)
            )

        row = cur.fetchone()
        if row is None:
            return None

        return self._row_to_version(row)

    def get_current_version_id(self, memory_id: str) -> Optional[str]:
        """获取当前版本ID"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM memory_versions WHERE memory_id = ? AND is_current = 1",
            (memory_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_version_count(self, memory_id: str, archived_only: bool = False) -> int:
        """
        获取版本总数

        Args:
            memory_id: 记忆ID
            archived_only: 是否只统计存档版本
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        sql = "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?"
        if archived_only:
            sql += " AND is_archived = 1"

        cur.execute(sql, (memory_id,))
        row = cur.fetchone()
        return row[0] if row else 0

    def get_update_sequence(self, memory_id: str) -> int:
        """获取当前更新序列号"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(update_sequence) FROM memory_versions WHERE memory_id = ?",
            (memory_id,)
        )
        row = cur.fetchone()
        return row[0] or 0

    def get_pending_updates_count(self, memory_id: str) -> int:
        """获取未存档的更新数"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ? AND is_archived = 0",
            (memory_id,)
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def _row_to_version(self, row: sqlite3.Row) -> MemoryVersion:
        """将数据库行转换为 MemoryVersion"""
        return MemoryVersion(
            id=row["id"],
            memory_id=row["memory_id"],
            version=row["version"],
            query_sentence=row["query_sentence"],
            content=row["content"] or "",
            lifecycle=row["lifecycle"] or 0,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            change_type=ChangeType(row["change_type"]),
            change_summary=row["change_summary"] or "",
            is_current=bool(row["is_current"]) if "is_current" in row.keys() else False,
        )

    # =========================================================================
    # 回滚
    # =========================================================================

    def rollback(
        self,
        memory_id: str,
        target_version: int = None,
    ) -> bool:
        """
        回滚到指定版本

        Args:
            memory_id: 记忆ID
            target_version: 目标版本号（None=上一版本）

        Returns:
            是否成功
        """
        # 1. 获取目标版本
        if target_version is None:
            # 获取上一版本（最新非当前版本）
            history = self.get_version_history(memory_id, limit=2, include_deleted=False)
            if len(history) < 2:
                return False
            target = history[1]  # 第二新的是上一版本
        else:
            target = self.get_version(memory_id, target_version)

        if target is None:
            return False

        # 2. 获取当前记忆
        current = self._vec_db._kv_read(memory_id)
        if current is None:
            return False

        # 3. 用目标版本数据更新 memories 表
        self._vec_db._kv_update(
            memory_id=memory_id,
            content=target.content,
            lifecycle=target.lifecycle,
        )

        # 4. 记录回滚版本
        version = self._get_next_version(memory_id)
        sequence = self._get_next_sequence(memory_id)
        version_id = self._generate_version_id(memory_id, version)
        now = datetime.now().isoformat()

        conn = self._vec_db._conn

        # 标记当前版本为非当前
        conn.cursor().execute("""
            UPDATE memory_versions
            SET is_current = 0
            WHERE memory_id = ? AND is_current = 1
        """, (memory_id,))

        # 插入回滚版本
        conn.cursor().execute("""
            INSERT INTO memory_versions (
                id, memory_id, version, query_sentence, content, lifecycle,
                created_at, updated_at, change_type, change_summary, is_current,
                is_archived, update_sequence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version_id,
            memory_id,
            version,
            target.query_sentence,
            target.content,
            target.lifecycle,
            target.created_at.isoformat(),
            now,
            ChangeType.ROLLBACK.value,
            f"回滚到 v{target.version}",
            1,
            1,  # is_archived
            sequence,
        ))
        conn.commit()

        return True

    # =========================================================================
    # 对比
    # =========================================================================

    def diff_versions(
        self,
        memory_id: str,
        v1: int,
        v2: int = None,
    ) -> VersionDiff:
        """
        对比两个版本的差异

        Args:
            memory_id: 记忆ID
            v1: 源版本号
            v2: 目标版本号（None=当前版本）

        Returns:
            VersionDiff
        """
        # 获取两个版本
        version1 = self.get_version(memory_id, v1)
        version2 = self.get_version(memory_id, v2)

        if version1 is None:
            raise ValueError(f"版本 {v1} 不存在")
        if version2 is None:
            raise ValueError(f"版本 {v2} 不存在")

        # 计算差异
        changes = {}
        fields = ["query_sentence", "content", "lifecycle"]

        for field in fields:
            old_val = getattr(version1, field)
            new_val = getattr(version2, field)
            if old_val != new_val:
                changes[field] = (old_val, new_val)

        # 生成摘要
        if not changes:
            summary = "两个版本完全相同"
        else:
            changed_fields = ", ".join(changes.keys())
            summary = f"共 {len(changes)} 处变化: {changed_fields}"

        return VersionDiff(
            memory_id=memory_id,
            from_version=v1,
            to_version=version2.version,
            changes=changes,
            summary=summary,
        )

    def diff_with_current(
        self,
        memory_id: str,
        version: int,
    ) -> VersionDiff:
        """
        与当前版本对比

        Args:
            memory_id: 记忆ID
            version: 要对比的历史版本号

        Returns:
            VersionDiff
        """
        return self.diff_versions(memory_id, v1=version, v2=None)

    # =========================================================================
    # 清理
    # =========================================================================

    def _auto_prune(self, memory_id: str) -> int:
        """自动清理旧版本（如果超过最大保留数）"""
        conn = self._vec_db._conn
        cur = conn.cursor()

        # 统计当前版本数
        count = self.get_version_count(memory_id)
        if count <= self._max_versions:
            return 0

        # 删除超过限制的旧版本
        delete_count = count - self._max_versions
        cur.execute("""
            DELETE FROM memory_versions
            WHERE id IN (
                SELECT id FROM memory_versions
                WHERE memory_id = ? AND is_current = 0 AND is_archived = 1
                ORDER BY version ASC
                LIMIT ?
            )
        """, (memory_id, delete_count))

        conn.commit()
        return cur.rowcount

    def delete_version_history(self, memory_id: str) -> int:
        """
        删除记忆的所有版本历史（危险操作）

        Args:
            memory_id: 记忆ID

        Returns:
            删除的版本数量
        """
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM memory_versions WHERE memory_id = ?",
            (memory_id,)
        )
        conn.commit()
        return cur.rowcount

    def prune_old_versions(
        self,
        memory_id: str,
        keep_count: int = 5,
    ) -> int:
        """
        清理旧版本（保留最近 N 个存档版本）

        Args:
            memory_id: 记忆ID
            keep_count: 保留的版本数量

        Returns:
            删除的版本数量
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        # 只删除非当前版本
        cur.execute("""
            DELETE FROM memory_versions
            WHERE memory_id = ?
            AND is_current = 0
            AND version <= (
                SELECT version
                FROM memory_versions
                WHERE memory_id = ?
                ORDER BY version DESC
                LIMIT 1 OFFSET ?
            )
        """, (memory_id, memory_id, keep_count))

        conn.commit()
        return cur.rowcount

    # =========================================================================
    # 统计
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        获取版本控制统计信息

        Returns:
            统计信息字典
        """
        conn = self._vec_db._conn
        cur = conn.cursor()

        # 总版本数
        cur.execute("SELECT COUNT(*) FROM memory_versions")
        total_versions = cur.fetchone()[0]

        # 总记忆数（去重）
        cur.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_versions")
        total_memories = cur.fetchone()[0]

        # 有未存档更新的记忆数
        cur.execute("SELECT COUNT(DISTINCT memory_id) FROM memory_versions WHERE is_archived = 0")
        pending_count = cur.fetchone()[0]

        return {
            "total_versions": total_versions,
            "total_memories": total_memories,
            "pending_archive_count": pending_count,
            "archive_threshold": self._archive_threshold,
            "max_versions_per_memory": self._max_versions,
        }