"""
星尘记忆 - 向量数据库 CRUD API

每个槽位建独立 Chroma Collection（64 维），实现精准子空间查找。
同时保留完整 384 维向量 Collection 用于全空间搜索。

架构：
├── 6 个槽位 Collection（各 64 维）：slot_scene / slot_subject / slot_action / slot_object / slot_purpose / slot_result
├── 1 个全量 Collection（384 维）：full_vectors
└── 1 个 KV 数据库（SQLite）：Memory 对象存储

子空间搜索流程：
1. 在查询涉及的每个槽位 Collection 中分别搜索 → 得到各槽位的 top_k 候选
2. 取各槽位候选集的 memory_id 交集
3. 对交集结果按 (match_count 降序, avg_distance 升序) 排序

全空间搜索流程：
1. 在 full_vectors Collection 中用完整 384 维向量搜索
2. 可选：用槽位匹配数+距离重排序
"""

import os
import uuid
import sqlite3
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

import numpy as np
import torch
import chromadb
from chromadb.config import Settings

from ..embedding_coder import InterestEncoder, QueryEncoderPipeline, QuerySlots, SLOT_NAMES, SLOT_DIM


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class Memory:
    """记忆数据模型"""
    id: str
    query_sentence: str           # "<场景><主体><动作><宾语><目的><结果>"
    query_embedding: np.ndarray   # 兴趣嵌入 [384]
    raw_embedding: np.ndarray     # 原始嵌入 [384]
    content: str
    lifecycle: int
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    extra: Dict[str, Any] = field(default_factory=dict)  # STO 元数据：权重、锁定、过期、血缘等

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query_sentence": self.query_sentence,
            "query_embedding": self.query_embedding.tolist(),
            "raw_embedding": self.raw_embedding.tolist(),
            "content": self.content,
            "lifecycle": self.lifecycle,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        return cls(
            id=d["id"],
            query_sentence=d["query_sentence"],
            query_embedding=np.array(d["query_embedding"], dtype=np.float32),
            raw_embedding=np.array(d["raw_embedding"], dtype=np.float32),
            content=d["content"],
            lifecycle=d["lifecycle"],
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            extra=d.get("extra", {}),
        )


@dataclass
class SearchResult:
    """搜索结果（不含 Memory 内容，内容由应用层根据 memory_id 获取）"""
    memory_id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)
    sort_by: Optional[str] = None   # "slot_match" 或 None
    match_count: int = 0            # 槽位值与查询值完全一致的个数
    avg_distance: float = 0.0       # 各槽位欧氏距离均值


# ============================================================================
# 嵌入模式
# ============================================================================

class EmbeddingMode:
    INTEREST = "interest"   # 兴趣嵌入（经过自注意力）
    RAW = "raw"             # 原始嵌入（不过自注意力）
    BOTH = "both"


# ============================================================================
# VecDBCRUD 主类
# ============================================================================

class VecDBCRUD:
    """
    向量数据库 CRUD API

    每个槽位独立 Chroma Collection（64 维），实现精准子空间查找。
    同时保留全量 Collection（384 维）用于全空间搜索。

    数据存储：
    - 6 个槽位 Collection: slot_{name}，各 64 维，metadata 含 {slot_name}: 字符串值
    - 1 个全量 Collection: full_vectors，384 维，metadata 含所有槽位字符串值
    - 1 个 KV 数据库 (SQLite): Memory 对象
    """

    SLOT_NAMES = SLOT_NAMES      # ["scene", "subject", "action", "object", "purpose", "result"]
    SLOT_DIM = SLOT_DIM          # 64
    FULL_DIM = SLOT_DIM * len(SLOT_NAMES)  # 384

    def __init__(
        self,
        persist_directory: str = "./data/xcmemory_db",
        vocab_size: int = 10000,
        archive_threshold: int = 10,
        max_versions_per_memory: int = 50,
        snapshot_write_threshold: int = 20,
        snapshot_idle_minutes: int = 30,
    ):
        """
        初始化 VecDBCRUD，自动创建所有数据库。

        Args:
            persist_directory: 持久化根目录，内部自动创建：
                - chroma_data/    Chroma 向量数据库
                - kv/             SQLite KV 数据库
            vocab_size: InterestEncoder 词汇表大小
            archive_threshold: 版本存档阈值，每 N 次更新存档一次
            max_versions_per_memory: 每条记忆最多保留的版本数
            snapshot_write_threshold: 快照写入阈值，每 N 次写入自动快照（0 禁用）
            snapshot_idle_minutes: 快照空闲阈值，空闲超过 N 分钟后下次写入前自动快照（0 禁用）
        """
        self.root = Path(persist_directory)
        self.root.mkdir(parents=True, exist_ok=True)

        chroma_dir = self.root / "chroma_data"
        kv_dir = self.root / "kv"
        kv_dir.mkdir(parents=True, exist_ok=True)

        # InterestEncoder + Pipeline
        self.encoder = InterestEncoder(vocab_size=vocab_size, slot_dim=self.SLOT_DIM, num_heads=4, num_layers=2)
        self.encoder.eval()
        self.pipeline = QueryEncoderPipeline(interest_encoder=self.encoder)

        # Chroma Client
        self._chroma_client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )

        # 6 个槽位 Collection（64 维）
        self._slot_collections: Dict[str, chromadb.Collection] = {}
        for slot in self.SLOT_NAMES:
            self._slot_collections[slot] = self._chroma_client.get_or_create_collection(
                name=f"slot_{slot}",
                metadata={"hnsw:space": "l2", "dim": self.SLOT_DIM},
            )

        # 全量 Collection（384 维）
        self._full_collection = self._chroma_client.get_or_create_collection(
            name="full_vectors",
            metadata={"hnsw:space": "l2", "dim": self.FULL_DIM},
        )

        # KV 数据库 (SQLite)
        self._db_path = kv_dir / "memory.db"
        self._init_kv_db()

        # 版本控制参数（传递给 VersionManager）
        self._archive_threshold = archive_threshold
        self._max_versions = max_versions_per_memory

        # 快照参数
        self._snapshot_write_threshold = snapshot_write_threshold
        self._snapshot_idle_minutes = snapshot_idle_minutes

        # 版本管理器（懒加载）
        self._version_manager = None
        # 快照管理器（懒加载）
        self._snapshot_manager = None

    @property
    def version_manager(self):
        """获取版本管理器（懒加载）"""
        if self._version_manager is None:
            from ..version_control import VersionManager
            self._version_manager = VersionManager(
                self,
                archive_threshold=self._archive_threshold,
                max_versions_per_memory=self._max_versions,
            )
        return self._version_manager

    @property
    def snapshot_manager(self):
        """获取快照管理器（懒加载）"""
        if self._snapshot_manager is None:
            from ..version_control.snapshot_manager import SnapshotManager
            self._snapshot_manager = SnapshotManager(
                self,
                write_threshold=self._snapshot_write_threshold,
                idle_minutes=self._snapshot_idle_minutes,
            )
        return self._snapshot_manager

    # =========================================================================
    # KV 数据库
    # =========================================================================

    def _init_kv_db(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                query_sentence TEXT NOT NULL,
                query_embedding BLOB NOT NULL,
                raw_embedding BLOB NOT NULL,
                content TEXT NOT NULL,
                lifecycle INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                extra TEXT NOT NULL DEFAULT '{}'
            )
        """)
        # 迁移：旧数据库缺少 extra 列时自动补上
        try:
            cur.execute("ALTER TABLE memories ADD COLUMN extra TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # 列已存在
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lifecycle ON memories(lifecycle)")

        # 槽位值反向索引表：支持"哪个槽位有哪个值"的查询
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slot_value_index (
                memory_id TEXT PRIMARY KEY,
                content TEXT,
                scene_value TEXT,
                subject_value TEXT,
                action_value TEXT,
                object_value TEXT,
                purpose_value TEXT,
                result_value TEXT,
                created_at TEXT,
                lifecycle INTEGER
            )
        """)
        # 迁移：旧表 time_value → scene_value 列名更改
        try:
            cur.execute("ALTER TABLE slot_value_index ADD COLUMN scene_value TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subject_value ON slot_value_index(subject_value)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_action_value ON slot_value_index(action_value)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_object_value ON slot_value_index(object_value)")

    def _kv_read(self, memory_id: str) -> Optional[Memory]:
        """内部方法：根据 memory_id 读取 Memory 对象（由 pyapi 暴露给应用层）"""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        if row is None:
            return None
        extra = {}
        if row["extra"]:
            try:
                extra = json.loads(row["extra"])
            except (json.JSONDecodeError, TypeError):
                extra = {}
        return Memory(
            id=row["id"],
            query_sentence=row["query_sentence"],
            query_embedding=np.frombuffer(row["query_embedding"], dtype=np.float32).copy(),
            raw_embedding=np.frombuffer(row["raw_embedding"], dtype=np.float32).copy(),
            content=row["content"],
            lifecycle=row["lifecycle"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            extra=extra,
        )

    def _kv_write(self, memory: Memory):
        extra_json = json.dumps(memory.extra, ensure_ascii=False)
        self._conn.cursor().execute("""
            INSERT INTO memories (
                id, query_sentence, query_embedding, raw_embedding,
                content, lifecycle, created_at, updated_at, extra
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            memory.id,
            memory.query_sentence,
            memory.query_embedding.tobytes(),
            memory.raw_embedding.tobytes(),
            memory.content,
            memory.lifecycle,
            memory.created_at.isoformat(),
            memory.updated_at.isoformat(),
            extra_json,
        ))
        self._conn.commit()

    def _kv_update(self, memory_id: str, content: Optional[str] = None, lifecycle: Optional[int] = None, extra: Optional[Dict[str, Any]] = None) -> bool:
        updates, params = [], []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if lifecycle is not None:
            updates.append("lifecycle = ?")
            params.append(lifecycle)
        if extra is not None:
            updates.append("extra = ?")
            params.append(json.dumps(extra, ensure_ascii=False))
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(memory_id)
        sql = f"UPDATE memories SET {', '.join(updates)} WHERE id = ?"
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._conn.commit()
        # SQLite rowcount may return 0 even when update succeeds (e.g., no actual change)
        # So we check if any rows matched by using changes() or simply return True if no error
        return cur.rowcount >= 0  # Return True as long as no error occurred

    def _kv_delete(self, memory_id: str) -> bool:
        self._conn.cursor().execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return self._conn.cursor().rowcount > 0

    # =========================================================================
    # 槽位值反向索引
    # =========================================================================

    def _update_slot_value_index(
        self,
        memory_id: str,
        content: str,
        slot_values: Dict[str, str],
        created_at: datetime,
        lifecycle: int,
    ):
        """更新槽位值索引表"""
        cur = self._conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO slot_value_index (
                memory_id, content,
                scene_value, subject_value, action_value, object_value,
                purpose_value, result_value, created_at, lifecycle
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            memory_id,
            content,
            slot_values.get("scene", ""),
            slot_values.get("subject", ""),
            slot_values.get("action", ""),
            slot_values.get("object", ""),
            slot_values.get("purpose", ""),
            slot_values.get("result", ""),
            created_at.isoformat(),
            lifecycle,
        ))
        self._conn.commit()

    def _delete_slot_value_index(self, memory_id: str):
        """从槽位值索引表删除"""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM slot_value_index WHERE memory_id = ?", (memory_id,))
        self._conn.commit()

    def find_memories_by_slot_value(
        self,
        word: str,
        slot: str,
        top_k: int = 100,
    ) -> List[str]:
        """
        按槽位值查找记忆（精确匹配）

        Args:
            word: 要查找的词
            slot: 槽位名（scene/subject/action/object/purpose/result）
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        if slot not in self.SLOT_NAMES:
            return []
        col_name = f"{slot}_value"
        cur = self._conn.cursor()
        cur.execute(f"""
            SELECT memory_id FROM slot_value_index
            WHERE {col_name} = ?
            LIMIT ?
        """, (word, top_k))
        return [row[0] for row in cur.fetchall()]

    def find_memories_by_any_slot(
        self,
        words: Dict[str, str],
        top_k: int = 100,
    ) -> List[str]:
        """
        按多个槽位值查找（AND 条件）

        Args:
            words: {slot_name: word, ...}

        Returns:
            memory_id 列表
        """
        if not words:
            return []
        cur = self._conn.cursor()
        conditions = []
        params = []
        for slot, word in words.items():
            if slot in self.SLOT_NAMES:
                conditions.append(f"{slot}_value = ?")
                params.append(word)
        if not conditions:
            return []
        sql = f"""
            SELECT memory_id FROM slot_value_index
            WHERE {' AND '.join(conditions)}
            LIMIT ?
        """
        params.append(top_k)
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]

    def get_memory_ids_with_slot_value(
        self,
        slot: str,
        top_k: int = 100,
    ) -> List[str]:
        """
        查找某槽位有值的记忆（不为空）

        Args:
            slot: 槽位名
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        if slot not in self.SLOT_NAMES:
            return []
        col_name = f"{slot}_value"
        cur = self._conn.cursor()
        cur.execute(f"""
            SELECT memory_id FROM slot_value_index
            WHERE {col_name} IS NOT NULL AND {col_name} != ''
            LIMIT ?
        """, (top_k,))
        return [row[0] for row in cur.fetchall()]

    def get_slot_value_stats(self) -> Dict[str, Dict[str, int]]:
        """
        获取槽位值统计信息

        Returns:
            {slot_name: {value: count, ...}, ...}
        """
        stats = {slot: {} for slot in self.SLOT_NAMES}
        cur = self._conn.cursor()
        for slot in self.SLOT_NAMES:
            col_name = f"{slot}_value"
            cur.execute(f"""
                SELECT {col_name}, COUNT(*) as cnt
                FROM slot_value_index
                WHERE {col_name} IS NOT NULL AND {col_name} != ''
                GROUP BY {col_name}
                ORDER BY cnt DESC
                LIMIT 50
            """)
            for row in cur.fetchall():
                if row[0]:
                    stats[slot][row[0]] = row[1]
        return stats

    # =========================================================================
    # 槽位解析
    # =========================================================================

    @staticmethod
    def _parse_query_sentence(query_sentence: str) -> List[str]:
        """解析 '<场景><主体><动作><宾语><目的><结果>' → [6个字符串]"""
        parts, current, in_bracket = [], "", False
        for ch in query_sentence:
            if ch == "<":
                in_bracket = True
                current = ""
            elif ch == ">":
                in_bracket = False
                parts.append(current)
            elif in_bracket:
                current += ch
        if len(parts) != 6:
            raise ValueError(f"查询句必须包含6个槽位，得到 {len(parts)} 个: {query_sentence}")
        return parts

    @staticmethod
    def _text_to_ids(text: str) -> torch.Tensor:
        if not text:
            return torch.tensor([[0]], dtype=torch.long)
        return torch.tensor([[ord(c) % 1000 for c in text]], dtype=torch.long)

    def _slots_from_sentence(self, query_sentence: str) -> QuerySlots:
        parts = self._parse_query_sentence(query_sentence)
        return QuerySlots(
            scene=self._text_to_ids(parts[0]),
            subject=self._text_to_ids(parts[1]),
            action=self._text_to_ids(parts[2]),
            object=self._text_to_ids(parts[3]),
            purpose=self._text_to_ids(parts[4]),
            result=self._text_to_ids(parts[5]),
        )

    def _slots_from_dict(self, query_slots: Dict[str, str]) -> QuerySlots:
        d = {}
        for slot in self.SLOT_NAMES:
            val = query_slots.get(slot)
            if val is not None:
                d[slot] = self._text_to_ids(val)
        return QuerySlots(**d)

    # =========================================================================
    # 写入
    # =========================================================================

    def write(
        self,
        query_sentence: str,
        content: str,
        lifecycle: int,
        embedding_mode: str = EmbeddingMode.INTEREST,
        record_version: bool = True,
    ) -> str:
        """
        写入一条记忆。

        会同时写入：
        - 6 个槽位 Collection（各存 64 维子向量 + 槽位字符串 metadata）
        - 1 个全量 Collection（384 维完整向量 + 全部槽位 metadata）
        - KV 数据库（Memory 对象）
        - 版本历史（遵循存档阈值策略）

        Args:
            query_sentence: 查询句
            content: 记忆内容
            lifecycle: 生命周期
            embedding_mode: 嵌入模式
            force_archive: 是否强制存档（默认 False，按阈值策略存档）
        """
        slots = self._slots_from_sentence(query_sentence)
        interest_vec = self.pipeline.encode(slots, use_raw=False, normalize=True)
        raw_vec = self.pipeline.encode(slots, use_raw=True, normalize=True)
        slot_vecs = self.pipeline.get_slot_vectors(slots)

        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        memory = Memory(
            id=memory_id,
            query_sentence=query_sentence,
            query_embedding=interest_vec,
            raw_embedding=raw_vec,
            content=content,
            lifecycle=lifecycle,
        )

        # 解析槽位字符串值
        parts = self._parse_query_sentence(query_sentence)
        slot_values = {name: parts[i] for i, name in enumerate(self.SLOT_NAMES)}

        # 选择用于全量 Collection 的向量
        full_vec = interest_vec if embedding_mode != EmbeddingMode.RAW else raw_vec

        # ---- 写入 6 个槽位 Collection ----
        for slot_name in self.SLOT_NAMES:
            vec_64 = slot_vecs[slot_name]  # [64]
            meta = {slot_name: slot_values[slot_name], "memory_id": memory_id}
            self._slot_collections[slot_name].add(
                ids=[memory_id],
                embeddings=[vec_64.tolist()],
                metadatas=[meta],
            )

        # ---- 写入全量 Collection ----
        full_meta = {"memory_id": memory_id}
        full_meta.update(slot_values)  # 所有槽位字符串值
        self._full_collection.add(
            ids=[memory_id],
            embeddings=[full_vec.tolist()],
            metadatas=[full_meta],
        )

        # ---- 写入 KV ----
        self._kv_write(memory)

        # ---- 写入槽位值反向索引表 ----
        self._update_slot_value_index(memory_id, content, slot_values, memory.created_at, lifecycle)

        # ---- 记录版本历史（创建版本总是存档）----
        try:
            self.version_manager.record_create(memory_id, memory)
        except Exception:
            # 版本记录失败不影响写入
            pass

        # ---- 快照检查 ----
        if self._snapshot_write_threshold > 0 or self._snapshot_idle_minutes > 0:
            try:
                self.snapshot_manager.on_write()
            except Exception:
                pass

        return memory_id

    # =========================================================================
    # 更新
    # =========================================================================

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        lifecycle: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
        force_archive: bool = False,
    ) -> bool:
        """
        更新记忆的 content、lifecycle 和/或 extra（不改变向量）

        版本更新遵循存档阈值策略：
        - 每 N 次更新存档一次（N 由 VersionManager.archive_threshold 设置）
        - 中间更新合并到当前存档版本，不产生新版本
        - 设置 force_archive=True 可强制存档当前版本

        Args:
            memory_id: 记忆ID
            content: 新内容（不修改则传 None）
            lifecycle: 新生命周期（不修改则传 None）
            extra: 新 extra 字典（不修改则传 None，会完整覆盖旧 extra）
            force_archive: 是否强制存档（默认 False，按阈值策略存档）

        Returns:
            是否成功
        """
        # 读取旧版本（用于版本记录）
        old_memory = self._kv_read(memory_id)

        # 执行更新
        ok = self._kv_update(memory_id, content=content, lifecycle=lifecycle, extra=extra)

        if ok and old_memory is not None:
            # 读取新版本并记录（按阈值策略）
            new_memory = self._kv_read(memory_id)
            if new_memory is not None:
                try:
                    self.version_manager.record_update(
                        memory_id=memory_id,
                        old_memory=old_memory,
                        new_memory=new_memory,
                        force_archive=force_archive,
                    )
                except Exception:
                    # 版本记录失败不影响更新操作
                    pass

        return ok

    # =========================================================================
    # 删除
    # =========================================================================

    def delete(self, memory_id: str, record_version: bool = True) -> bool:
        """
        删除记忆（从所有 Collection + KV 中删除）

        Args:
            memory_id: 记忆ID
            record_version: 是否记录删除版本（默认 True）

        Returns:
            是否删除成功
        """
        # 记录删除版本（在实际删除前）
        if record_version:
            try:
                self.version_manager.record_delete(memory_id)
            except Exception:
                # 版本记录失败不影响删除操作
                pass

        # 从 6 个槽位 Collection 删除
        for slot_name in self.SLOT_NAMES:
            try:
                self._slot_collections[slot_name].delete(ids=[memory_id])
            except Exception:
                pass
        # 从全量 Collection 删除
        try:
            self._full_collection.delete(ids=[memory_id])
        except Exception:
            pass
        # 从 KV 删除
        ok = self._kv_delete(memory_id)

        # 从槽位值索引删除
        self._delete_slot_value_index(memory_id)

        return ok

    # =========================================================================
    # 子空间搜索
    # =========================================================================

    def search_subspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        use_slot_rerank: bool = True,
    ) -> List[SearchResult]:
        """
        子空间搜索：在每个查询槽位的独立 64 维 Collection 中搜索，取交集后排序。

        流程：
        1. 对查询中每个有值的槽位，在对应的 slot_{name} Collection 中搜索
        2. 取所有槽位搜索结果的 memory_id 交集
        3. 如果 use_slot_rerank=True：
           - 计算 match_count（槽位字符串值与查询完全一致的个数）
           - 按 (match_count 降序, avg_distance 升序) 排序
        4. 否则按 avg_distance 升序

        Args:
            query_slots: {"subject": "我", "purpose": "锻炼身体", ...}
            top_k: 返回数量
            use_slot_rerank: 是否用槽位匹配+距离排序
        """
        slots = self._slots_from_dict(query_slots)
        slot_vecs = self.pipeline.get_slot_vectors(slots)

        # 只取有值的槽位
        active_slots = {
            name: slot_vecs[name]
            for name in self.SLOT_NAMES
            if name in query_slots and query_slots.get(name) and np.linalg.norm(slot_vecs[name]) > 0
        }
        if not active_slots:
            return []

        # ---- 各槽位独立搜索 ----
        slot_results: Dict[str, List[Dict[str, Any]]] = {}   # slot_name -> [{memory_id, distance, metadata}]
        for slot_name, vec_64 in active_slots.items():
            collection = self._slot_collections[slot_name]
            n_results = max(top_k * 5, 50)  # 多取一些候选
            try:
                chroma_res = collection.query(
                    query_embeddings=[vec_64.tolist()],
                    n_results=n_results,
                    include=["metadatas", "distances"],
                )
            except Exception:
                continue

            results = []
            if chroma_res["ids"] and len(chroma_res["ids"]) > 0:
                for i, mid in enumerate(chroma_res["ids"][0]):
                    results.append({
                        "memory_id": mid,
                        "distance": chroma_res["distances"][0][i] if chroma_res["distances"] else 0.0,
                        "metadata": chroma_res["metadatas"][0][i] if chroma_res["metadatas"] else {},
                    })
            slot_results[slot_name] = results

        if not slot_results:
            return []

        # ---- 取交集 ----
        # 收集每个槽位搜索到的 memory_id 集合
        id_sets = [set(r["memory_id"] for r in results) for results in slot_results.values()]
        common_ids = set.intersection(*id_sets) if id_sets else set()

        # 如果交集为空，降级为并集（至少返回一些结果）
        if not common_ids:
            common_ids = set.union(*id_sets) if id_sets else set()

        if not common_ids:
            return []

        # ---- 构建距离信息 ----
        # memory_id -> {slot_name: distance}
        mem_slot_dists: Dict[str, Dict[str, float]] = {}
        for slot_name, results in slot_results.items():
            for r in results:
                mid = r["memory_id"]
                if mid in common_ids:
                    if mid not in mem_slot_dists:
                        mem_slot_dists[mid] = {}
                    mem_slot_dists[mid][slot_name] = r["distance"]

        # ---- 获取 metadata（用于计算 match_count）----
        # 从全量 Collection 中获取完整 metadata
        candidates = []
        for mid in common_ids:
            try:
                full_res = self._full_collection.get(ids=[mid], include=["metadatas"])
                metadata = full_res["metadatas"][0] if full_res["metadatas"] and len(full_res["metadatas"]) > 0 else {}
            except Exception:
                metadata = {}

            # 计算 match_count：槽位字符串值与查询完全一致的个数
            match_count = 0
            for slot_name in active_slots:
                query_val = query_slots.get(slot_name, "")
                stored_val = metadata.get(slot_name, "")
                if query_val and stored_val and query_val == stored_val:
                    match_count += 1

            dists = mem_slot_dists.get(mid, {})
            avg_dist = float(np.mean(list(dists.values()))) if dists else float("inf")

            candidates.append({
                "memory_id": mid,
                "avg_distance": avg_dist,
                "match_count": match_count,
                "metadata": metadata,
            })

        # ---- 排序 ----
        if use_slot_rerank:
            candidates.sort(key=lambda x: (-x["match_count"], x["avg_distance"]))
        else:
            candidates.sort(key=lambda x: x["avg_distance"])

        # ---- 构建返回 ----
        search_results = []
        for c in candidates[:top_k]:
            sr = SearchResult(
                memory_id=c["memory_id"],
                distance=c["avg_distance"],
                metadata=c.get("metadata", {}),
                sort_by="slot_match" if use_slot_rerank else None,
                match_count=c["match_count"],
                avg_distance=c["avg_distance"],
            )
            search_results.append(sr)

        return search_results

    # =========================================================================
    # 全空间搜索
    # =========================================================================

    def search_fullspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        embedding_mode: str = EmbeddingMode.INTEREST,
        use_slot_rerank: bool = False,
    ) -> List[SearchResult]:
        """
        全空间搜索：在 full_vectors Collection（384 维）中搜索。

        Args:
            query_slots: {"subject": "我", "purpose": "锻炼身体", ...}
            top_k: 返回数量
            embedding_mode: INTEREST 或 RAW
            use_slot_rerank: 是否用槽位匹配+距离排序
        """
        slots = self._slots_from_dict(query_slots)
        if embedding_mode == EmbeddingMode.RAW:
            vec = self.pipeline.encode(slots, use_raw=True, normalize=True)
        else:
            vec = self.pipeline.encode(slots, use_raw=False, normalize=True)

        n_results = top_k * 10 if use_slot_rerank else top_k
        try:
            chroma_res = self._full_collection.query(
                query_embeddings=[vec.tolist()],
                n_results=n_results,
                include=["metadatas", "distances"],
            )
        except Exception:
            return []

        if not chroma_res["ids"] or len(chroma_res["ids"]) == 0:
            return []

        results = []
        for i, mid in enumerate(chroma_res["ids"][0]):
            meta = chroma_res["metadatas"][0][i] if chroma_res["metadatas"] else {}
            dist = chroma_res["distances"][0][i] if chroma_res["distances"] else 0.0
            results.append({
                "memory_id": mid,
                "distance": dist,
                "metadata": meta,
            })

        if not use_slot_rerank:
            search_results = []
            for r in results[:top_k]:
                sr = SearchResult(
                    memory_id=r["memory_id"],
                    distance=r["distance"],
                    metadata=r.get("metadata", {}),
                    sort_by=None,
                )
                search_results.append(sr)
            return search_results

        # 使用槽位排序
        active_slots = {name: query_slots[name] for name in self.SLOT_NAMES if name in query_slots and query_slots.get(name)}
        candidates = []
        for r in results:
            metadata = r.get("metadata", {})
            match_count = 0
            for slot_name in active_slots:
                query_val = query_slots.get(slot_name, "")
                stored_val = metadata.get(slot_name, "")
                if query_val and stored_val and query_val == stored_val:
                    match_count += 1
            candidates.append({
                "memory_id": r["memory_id"],
                "distance": r["distance"],
                "metadata": metadata,
                "match_count": match_count,
            })

        candidates.sort(key=lambda x: (-x["match_count"], x["distance"]))

        search_results = []
        for c in candidates[:top_k]:
            sr = SearchResult(
                memory_id=c["memory_id"],
                distance=c["distance"],
                metadata=c.get("metadata", {}),
                sort_by="slot_match",
                match_count=c["match_count"],
            )
            search_results.append(sr)

        return search_results

    # =========================================================================
    # 工具方法
    # =========================================================================

    def count(self) -> int:
        """返回记忆总数（从 KV 数据库计数）"""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM memories")
        return cur.fetchone()[0]

    def exists(self, memory_id: str) -> bool:
        """检查记忆是否存在"""
        return self._kv_read(memory_id) is not None

    def clear(self):
        """清空所有数据（危险操作）"""
        # 清空 KV
        self._conn.cursor().execute("DELETE FROM memories")
        self._conn.commit()

        # 清空所有 Chroma Collection
        for slot_name in self.SLOT_NAMES:
            try:
                self._chroma_client.delete_collection(name=f"slot_{slot_name}")
                self._slot_collections[slot_name] = self._chroma_client.get_or_create_collection(
                    name=f"slot_{slot_name}",
                    metadata={"hnsw:space": "l2", "dim": self.SLOT_DIM},
                )
            except Exception:
                pass

        try:
            self._chroma_client.delete_collection(name="full_vectors")
            self._full_collection = self._chroma_client.get_or_create_collection(
                name="full_vectors",
                metadata={"hnsw:space": "l2", "dim": self.FULL_DIM},
            )
        except Exception:
            pass

        # 重置快照计数器
        if self._snapshot_manager is not None:
            self._snapshot_manager._pending_count = 0

    def close(self):
        """关闭所有连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._chroma_client = None
        self._slot_collections = {}
        self._full_collection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # =========================================================================
    # 便捷属性
    # =========================================================================

    @property
    def slot_counts(self) -> Dict[str, int]:
        """各槽位 Collection 中的向量数"""
        return {name: col.count() for name, col in self._slot_collections.items()}

    @property
    def full_count(self) -> int:
        """全量 Collection 中的向量数"""
        return self._full_collection.count() if self._full_collection else 0
