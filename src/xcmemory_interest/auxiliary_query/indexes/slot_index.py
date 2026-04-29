"""
SlotIndex - 查询句槽位索引
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import chromadb
from chromadb.config import Settings

from ..storage.sql_db import SQLDatabase
from ..storage.kv_db import KVDatabase


# 槽位名称列表
SLOT_NAMES = ["scene", "subject", "action", "object", "purpose", "result"]
SLOT_DIM = 64


class SlotIndex:
    """
    查询句槽位索引

    使用原始嵌入（RawEmbedding）构建按槽位分区的 ANN 索引。

    索引结构：
        6 个 Chroma Collection（各 64 维）：
            slot_scene, slot_subject, slot_action,
            slot_object, slot_purpose, slot_result

        每条记录包含：
            - memory_id
            - slot_vector (64维)
            - slot_value (字符串)
    """

    def __init__(
        self,
        chroma_path: str,
        sql_db: SQLDatabase,
        slot_dim: int = 64,
    ):
        """
        初始化槽位索引

        Args:
            chroma_path: Chroma 持久化路径
            sql_db: SQL 数据库（用于存储 metadata）
            slot_dim: 槽位向量维度
        """
        self.chroma_path = chroma_path
        self.sql_db = sql_db
        self.slot_dim = slot_dim

        # 初始化 Chroma Client
        self._chroma = chromadb.PersistentClient(
            path=chroma_path,
            settings=Settings(anonymized_telemetry=False),
        )

        # 6 个槽位 Collection
        self._slot_collections: Dict[str, chromadb.Collection] = {}
        for slot in SLOT_NAMES:
            self._slot_collections[slot] = self._chroma.get_or_create_collection(
                name=f"slot_{slot}",
                metadata={"hnsw:space": "l2", "dim": slot_dim},
            )

        # SQL 表用于存储 metadata
        self._init_tables()

    def _init_tables(self):
        """初始化元数据表"""
        self.sql_db.create_table(
            "slot_metadata",
            {
                "memory_id": "TEXT PRIMARY KEY",
                "slot_scene": "TEXT",
                "slot_subject": "TEXT",
                "slot_action": "TEXT",
                "slot_object": "TEXT",
                "slot_purpose": "TEXT",
                "slot_result": "TEXT",
            },
            if_not_exists=True,
        )
        # 迁移：旧表 time→scene 重命名
        try:
            self.sql_db._conn.cursor().execute(
                "ALTER TABLE slot_metadata ADD COLUMN slot_scene TEXT"
            )
            self.sql_db._auto_commit()
        except Exception:
            pass  # 列已存在

    def add(
        self,
        memory_id: str,
        slot_vectors: Dict[str, np.ndarray],
        slot_values: Dict[str, str],
    ):
        """
        写入时注册槽位索引

        Args:
            memory_id: 记忆 ID
            slot_vectors: 各槽位向量，如 {"scene": [64维], "subject": [64维], ...}
            slot_values: 各槽位字符串值，如 {"scene": "平时", "subject": "我", ...}
        """
        # 写入各槽位 Collection
        for slot_name in SLOT_NAMES:
            if slot_name not in slot_vectors:
                continue

            vec = slot_vectors[slot_name]
            if isinstance(vec, np.ndarray):
                vec = vec.flatten().tolist()

            value = slot_values.get(slot_name, "")

            self._slot_collections[slot_name].add(
                ids=[memory_id],
                embeddings=[vec],
                metadatas=[{"slot_value": value, "memory_id": memory_id}],
            )

        # 写入 metadata
        metadata = {"memory_id": memory_id}
        for slot_name in SLOT_NAMES:
            metadata[f"slot_{slot_name}"] = slot_values.get(slot_name, "")
        self.sql_db.insert("slot_metadata", metadata, or_replace=True)

    def remove(self, memory_id: str):
        """
        删除记忆的槽位索引

        Args:
            memory_id: 记忆 ID
        """
        for slot_name in SLOT_NAMES:
            try:
                self._slot_collections[slot_name].delete(ids=[memory_id])
            except Exception:
                pass

        self.sql_db.delete("slot_metadata", {"memory_id": memory_id})

    def remove_batch(self, memory_ids: List[str]):
        """批量删除槽位索引"""
        if not memory_ids:
            return
        for slot_name in SLOT_NAMES:
            try:
                self._slot_collections[slot_name].delete(ids=memory_ids)
            except Exception:
                pass
        placeholders = ",".join("?" for _ in memory_ids)
        self.sql_db._conn.execute(f"DELETE FROM slot_metadata WHERE memory_id IN ({placeholders})", memory_ids)
        self.sql_db._conn.commit()

    def find_by_word(
        self,
        word: str,
        slot: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        按词查找（在指定槽位中搜索）

        注意：这个方法需要先将词转换为向量才能搜索。
        简化实现：直接在 metadata 中精确匹配。

        Args:
            word: 要查找的词
            slot: 槽位名（scene/subject/action/object/purpose/result）
            top_k: 返回数量

        Returns:
            [(memory_id, distance), ...]
        """
        if slot not in SLOT_NAMES:
            return []

        # 简化实现：在 metadata 中查找
        results = self.sql_db.select(
            "slot_metadata",
            columns=["memory_id", f"slot_{slot}"],
            where={f"slot_{slot}": word},
            limit=top_k,
        )

        # 返回匹配结果，距离设为 0（精确匹配）
        return [(r["memory_id"], 0.0) for r in results]

    def find_by_vector(
        self,
        vector: np.ndarray,
        slot: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        按向量查找（在指定槽位中搜索）

        Args:
            vector: 查询向量 [64维]
            slot: 槽位名
            top_k: 返回数量

        Returns:
            [(memory_id, distance), ...]
        """
        if slot not in SLOT_NAMES:
            return []

        vec_list = vector.flatten().tolist() if isinstance(vector, np.ndarray) else vector

        try:
            res = self._slot_collections[slot].query(
                query_embeddings=[vec_list],
                n_results=top_k,
                include=["metadatas", "distances"],
            )
        except Exception:
            return []

        results = []
        if res["ids"] and len(res["ids"]) > 0:
            for i, mid in enumerate(res["ids"][0]):
                dist = res["distances"][0][i] if res["distances"] else 0.0
                results.append((mid, float(dist)))
        return results

    def find_in_all_slots(
        self,
        word: str,
        top_k: int = 5,
    ) -> Dict[str, List[Tuple[str, float]]]:
        """
        在所有槽位中查找词

        Args:
            word: 要查找的词
            top_k: 每槽位返回数量

        Returns:
            {slot_name: [(memory_id, distance), ...], ...}
        """
        results = {}
        for slot in SLOT_NAMES:
            slot_results = self.find_by_word(word, slot, top_k)
            if slot_results:
                results[slot] = slot_results
        return results

    def get_slot_value(self, memory_id: str, slot: str) -> Optional[str]:
        """
        获取记忆指定槽位的字符串值

        Args:
            memory_id: 记忆 ID
            slot: 槽位名

        Returns:
            槽位值，不存在返回 None
        """
        results = self.sql_db.select(
            "slot_metadata",
            columns=[f"slot_{slot}"],
            where={"memory_id": memory_id},
            limit=1,
        )
        if not results:
            return None
        return results[0].get(f"slot_{slot}")

    def get_all_slot_values(self, memory_id: str) -> Dict[str, str]:
        """
        获取记忆所有槽位的字符串值

        Args:
            memory_id: 记忆 ID

        Returns:
            {slot_name: value, ...}
        """
        results = self.sql_db.select(
            "slot_metadata",
            where={"memory_id": memory_id},
            limit=1,
        )
        if not results:
            return {}
        r = results[0]
        return {slot: r.get(f"slot_{slot}", "") for slot in SLOT_NAMES}

    def count(self, slot: str = None) -> int:
        """
        返回索引中的记录数

        Args:
            slot: 槽位名（None 表示所有槽位总数）

        Returns:
            记录数
        """
        if slot and slot in SLOT_NAMES:
            return self._slot_collections[slot].count()
        elif slot is None:
            return sum(col.count() for col in self._slot_collections.values())
        return 0

    def clear(self):
        """清空所有槽位索引"""
        # 清空 Chroma
        for slot_name in SLOT_NAMES:
            try:
                self._chroma.delete_collection(name=f"slot_{slot_name}")
                self._slot_collections[slot_name] = self._chroma.get_or_create_collection(
                    name=f"slot_{slot_name}",
                    metadata={"hnsw:space": "l2", "dim": self.slot_dim},
                )
            except Exception:
                pass

        # 清空 SQL metadata
        self.sql_db.clear("slot_metadata")

    def close(self):
        """关闭连接"""
        self._chroma = None
        self._slot_collections = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
