# -*- coding: utf-8 -*-
"""
星尘记忆 — 数据库级快照管理器

支持：
- 自动快照：每 N 次写入或空闲超过 T 分钟后下次写入前自动创建快照
- 手动快照：调用 create_snapshot() 随时创建
- 快照回滚：restore_snapshot() 恢复到任意快照点
- 快照管理：list/delete 快照

配置：
- write_threshold: 累计写入次数触发快照（默认 20）
- idle_minutes: 上次写入后空闲超过 N 分钟触发快照（默认 30）
"""

from __future__ import annotations

import uuid
import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING

from ..basic_crud.vec_db_crud import EmbeddingMode, SearchResult

if TYPE_CHECKING:
    from ..basic_crud.vec_db_crud import VecDBCRUD


class SnapshotManager:
    """
    数据库级快照管理器

    在 VecDBCRUD 每次写入后调用 on_write()，
    达到阈值时自动创建全库快照。
    """

    def __init__(
        self,
        vec_db: "VecDBCRUD",
        write_threshold: int = 20,
        idle_minutes: int = 30,
    ):
        self._vec_db = vec_db
        self._write_threshold = write_threshold
        self._idle_minutes = idle_minutes

        # 运行时计数
        self._pending_count = 0
        self._last_write_time: Optional[datetime] = None

        # 初始化表
        self._init_tables()
        # 从数据库恢复计数器状态
        self._restore_pending_state()

    # =========================================================================
    # 表初始化
    # =========================================================================

    def _init_tables(self):
        conn = self._vec_db._conn
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS db_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                memory_count INTEGER NOT NULL,
                trigger_reason TEXT NOT NULL DEFAULT 'manual'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshot_memories (
                snapshot_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                query_sentence TEXT NOT NULL,
                content TEXT NOT NULL,
                lifecycle INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                extra TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (snapshot_id, memory_id)
            )
        """)

        conn.commit()

    def _restore_pending_state(self):
        """从数据库恢复上次写入计数和时间（重启后保持连续）"""
        conn = self._vec_db._conn
        cur = conn.cursor()

        # 找到最近一次快照之后写入的条数
        cur.execute("SELECT created_at FROM db_snapshots ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            last_snapshot_time = datetime.fromisoformat(row["created_at"])
            cur.execute(
                "SELECT COUNT(*) as cnt, MAX(updated_at) as last_update FROM memories WHERE created_at > ?",
                (last_snapshot_time.isoformat(),),
            )
            count_row = cur.fetchone()
            if count_row:
                self._pending_count = count_row["cnt"] or 0
                if count_row["last_update"]:
                    self._last_write_time = datetime.fromisoformat(count_row["last_update"])

    # =========================================================================
    # 写入钩子
    # =========================================================================

    def on_write(self):
        """每次写入后调用，检查是否需要自动快照"""
        self._pending_count += 1
        now = datetime.now()

        should_snapshot = False
        reason = ""

        if self._write_threshold > 0 and self._pending_count >= self._write_threshold:
            should_snapshot = True
            reason = f"write_threshold({self._pending_count}>={self._write_threshold})"
        elif self._idle_minutes > 0 and self._last_write_time is not None:
            gap_minutes = (now - self._last_write_time).total_seconds() / 60
            if gap_minutes >= self._idle_minutes:
                should_snapshot = True
                reason = f"idle({gap_minutes:.0f}min>={self._idle_minutes}min)"

        self._last_write_time = now

        if should_snapshot:
            self.create_snapshot(trigger_reason=reason)

    # =========================================================================
    # 快照创建
    # =========================================================================

    def create_snapshot(self, trigger_reason: str = "manual") -> str:
        """
        创建当前数据库状态的快照。

        Args:
            trigger_reason: 触发原因（manual / write_threshold / idle）

        Returns:
            snapshot_id
        """
        snapshot_id = f"snap_{uuid.uuid4().hex[:12]}"
        now = datetime.now()
        conn = self._vec_db._conn

        # 读取当前所有记忆
        memories = self._vec_db._kv_read_all()
        memory_count = len(memories)

        cur = conn.cursor()

        # 写入快照元数据
        cur.execute(
            "INSERT INTO db_snapshots (snapshot_id, created_at, memory_count, trigger_reason) VALUES (?, ?, ?, ?)",
            (snapshot_id, now.isoformat(), memory_count, trigger_reason),
        )

        # 写入每条记忆的完整数据
        for mem in memories:
            extra_json = json.dumps(mem.extra, ensure_ascii=False)
            cur.execute(
                """INSERT INTO snapshot_memories
                   (snapshot_id, memory_id, query_sentence, content, lifecycle,
                    created_at, updated_at, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    mem.id,
                    mem.query_sentence,
                    mem.content,
                    mem.lifecycle,
                    mem.created_at.isoformat(),
                    mem.updated_at.isoformat(),
                    extra_json,
                ),
            )

        conn.commit()

        # 重置计数器
        self._pending_count = 0

        return snapshot_id

    # =========================================================================
    # 快照查询
    # =========================================================================

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """列出所有快照元数据"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_id, created_at, memory_count, trigger_reason "
            "FROM db_snapshots ORDER BY created_at DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """获取单个快照元数据"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_id, created_at, memory_count, trigger_reason "
            "FROM db_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_snapshot_memories(self, snapshot_id: str) -> List[Dict[str, Any]]:
        """获取快照中的所有记忆数据"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM snapshot_memories WHERE snapshot_id = ? ORDER BY created_at",
            (snapshot_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    # =========================================================================
    # 快照回滚
    # =========================================================================

    def restore_snapshot(self, snapshot_id: str) -> bool:
        """
        回滚到指定快照。

        流程：
        1. 清空当前所有记忆（SQLite + ChromaDB + 索引）
        2. 从快照表逐条恢复记忆（保留原始 memory_id）
        3. 重置写入计数器
        """
        snap = self.get_snapshot(snapshot_id)
        if snap is None:
            return False

        memories = self.get_snapshot_memories(snapshot_id)

        # 清空当前数据
        self._vec_db.clear()

        # 逐条恢复（如果快照为空则恢复后就是空数据库）
        for mem_data in memories:
            self._restore_single_memory(mem_data)

        # 重置计数器
        self._pending_count = 0
        self._last_write_time = datetime.now()

        return True

    def _restore_single_memory(self, mem_data: Dict[str, Any]):
        """
        恢复单条记忆到数据库。

        使用 VecDBCRUD 的编码管线重新生成向量，
        但保留原始 memory_id、时间戳等。
        """
        query_sentence = mem_data["query_sentence"]
        content = mem_data["content"]
        lifecycle = mem_data["lifecycle"]
        memory_id = mem_data["memory_id"]
        created_at_str = mem_data.get("created_at", datetime.now().isoformat())
        updated_at_str = mem_data.get("updated_at", datetime.now().isoformat())
        extra_json = mem_data.get("extra", "{}")

        try:
            extra = json.loads(extra_json)
        except (json.JSONDecodeError, TypeError):
            extra = {}

        # 编码
        slots = self._vec_db._slots_from_sentence(query_sentence)
        interest_vec = self._vec_db.pipeline.encode(slots, use_raw=False, normalize=True)
        raw_vec = self._vec_db.pipeline.encode(slots, use_raw=True, normalize=True)
        slot_vecs = self._vec_db.pipeline.get_slot_vectors(slots)

        # 解析槽位字符串值
        parts = self._vec_db._parse_query_sentence(query_sentence)
        slot_values = {name: parts[i] for i, name in enumerate(self._vec_db.SLOT_NAMES)}

        # 写入 6 个槽位 Collection
        for slot_name in self._vec_db.SLOT_NAMES:
            if slot_name in slot_vecs:
                meta = {slot_name: slot_values[slot_name], "memory_id": memory_id}
                self._vec_db._slot_collections[slot_name].add(
                    ids=[memory_id],
                    embeddings=[slot_vecs[slot_name].tolist()],
                    metadatas=[meta],
                )

        # 写入全量 Collection
        full_meta = {"memory_id": memory_id}
        full_meta.update(slot_values)
        self._vec_db._full_collection.add(
            ids=[memory_id],
            embeddings=[interest_vec.tolist()],
            metadatas=[full_meta],
        )

        # 写入 KV (SQLite)
        self._vec_db._conn.cursor().execute(
            """INSERT INTO memories
               (id, query_sentence, query_embedding, raw_embedding, content,
                lifecycle, created_at, updated_at, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                query_sentence,
                interest_vec.tobytes(),
                raw_vec.tobytes(),
                content,
                lifecycle,
                created_at_str,
                updated_at_str,
                json.dumps(extra, ensure_ascii=False),
            ),
        )
        self._vec_db._conn.commit()

        # 写入槽位值索引
        try:
            ca = datetime.fromisoformat(created_at_str)
        except (ValueError, TypeError):
            ca = datetime.now()
        self._vec_db._update_slot_value_index(
            memory_id, content, slot_values, ca, lifecycle,
        )

    # =========================================================================
    # 快照删除
    # =========================================================================

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除指定快照及其记忆数据"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute("DELETE FROM snapshot_memories WHERE snapshot_id = ?", (snapshot_id,))
        cur.execute("DELETE FROM db_snapshots WHERE snapshot_id = ?", (snapshot_id,))
        conn.commit()
        return cur.rowcount > 0

    def delete_all_snapshots(self):
        """删除所有快照"""
        conn = self._vec_db._conn
        cur = conn.cursor()
        cur.execute("DELETE FROM snapshot_memories")
        cur.execute("DELETE FROM db_snapshots")
        conn.commit()

    # =========================================================================
    # 状态查询
    # =========================================================================

    @property
    def pending_count(self) -> int:
        """自上次快照以来的写入计数"""
        return self._pending_count

    @property
    def last_write_time(self) -> Optional[datetime]:
        """上次写入时间"""
        return self._last_write_time

    def get_status(self) -> Dict[str, Any]:
        """获取快照管理器状态"""
        return {
            "pending_count": self._pending_count,
            "write_threshold": self._write_threshold,
            "idle_minutes": self._idle_minutes,
            "last_write_time": self._last_write_time.isoformat() if self._last_write_time else None,
            "total_snapshots": len(self.list_snapshots()),
        }
