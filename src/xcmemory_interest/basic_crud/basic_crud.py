"""
星尘记忆 - 基础增删查改模块

提供记忆的基础 CRUD 操作接口，支持两种嵌入模式：
- 兴趣嵌入（InterestEmbedding）：经过 InterestEncoder 自注意力处理
- 原始嵌入（RawEmbedding）：不过自注意力，直接拼接

写入时：
- query_sentence → 生成两种向量 → 存入 vector_db
- Memory 对象存入 KV 数据库

读取时：
- 从 vector_db 检索 memory_id
- 从 KV 数据库获取 Memory 对象
"""

import os
import json
import sqlite3
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

import numpy as np
import torch

from ..embedding_coder import InterestEncoder, QueryEncoderPipeline, QuerySlots
from ..config import DEVICE


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class Memory:
    """记忆数据模型"""
    id: str                          # 记忆唯一ID
    query_sentence: str               # 查询句 "<时间><主体><动作><宾语><目的><结果>"
    query_embedding: np.ndarray      # 兴趣嵌入 [384]（经过自注意力）
    raw_embedding: np.ndarray        # 原始嵌入 [384]（不过自注意力）
    content: str                     # 记忆内容
    lifecycle: int                    # 生命周期
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "query_sentence": self.query_sentence,
            "query_embedding": self.query_embedding.tolist(),
            "raw_embedding": self.raw_embedding.tolist(),
            "content": self.content,
            "lifecycle": self.lifecycle,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        """从字典反序列化"""
        return cls(
            id=d["id"],
            query_sentence=d["query_sentence"],
            query_embedding=np.array(d["query_embedding"], dtype=np.float32),
            raw_embedding=np.array(d["raw_embedding"], dtype=np.float32),
            content=d["content"],
            lifecycle=d["lifecycle"],
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
        )


@dataclass
class SearchResult:
    """搜索结果（不含 Memory 内容，内容由应用层根据 memory_id 获取）"""
    memory_id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)
    # 排序依据（用于展示）
    sort_by: Optional[str] = None  # 排序依据："slot_match" 或 None
    match_count: int = 0  # 查询槽位在记忆中对应的数量
    avg_distance: float = 0.0  # 平均欧氏距离


# ============================================================================
# 嵌入模式枚举
# ============================================================================

class EmbeddingMode:
    """嵌入模式"""
    INTEREST = "interest"      # 兴趣嵌入（经过自注意力）
    RAW = "raw"               # 原始嵌入（不过自注意力）
    BOTH = "both"             # 两者都用


# ============================================================================
# BasicCRUD 主类
# ============================================================================

class BasicCRUD:
    """
    基础增删查改模块

    支持两种嵌入模式进行向量搜索：
    - INTEREST: 兴趣嵌入，经过 InterestEncoder 自注意力处理
    - RAW: 原始嵌入，不过自注意力，直接拼接

    数据存储：
    - KV数据库（SQLite）: Memory 对象
    - 向量数据库（Chroma）: 向量 + metadata
    """

    def __init__(
        self,
        persist_directory: str = "./data/xcmemory_kv",
        vector_db_path: str = "./data/vector_db",
        vocab_size: int = 10000,
        embedding_dim: int = 384,
        slot_dim: int = 64,
    ):
        """
        初始化 BasicCRUD

        Args:
            persist_directory: KV数据库持久化目录
            vector_db_path: 向量数据库目录
            vocab_size: 词汇表大小
            embedding_dim: 嵌入维度
            slot_dim: 槽位维度
        """
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 初始化 InterestEncoder 和 QueryEncoderPipeline
        self.encoder = InterestEncoder(
            vocab_size=vocab_size,
            slot_dim=slot_dim,
            num_heads=4,
            num_layers=2,
        )
        self.encoder.eval()
        self.pipeline = QueryEncoderPipeline(interest_encoder=self.encoder, device=DEVICE)

        # KV 数据库（SQLite）
        self.db_path = self.persist_directory / "memory.db"
        self._init_kv_db()

        # 向量数据库（延迟导入，避免循环依赖）
        self._vector_db = None
        self._vector_db_path = vector_db_path

    def _init_kv_db(self):
        """初始化 KV 数据库"""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-8000")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA mmap_size=268435456")
        self.cursor = self.conn.cursor()

        # 创建表
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                query_sentence TEXT NOT NULL,
                query_embedding BLOB NOT NULL,
                raw_embedding BLOB NOT NULL,
                content TEXT NOT NULL,
                lifecycle INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # 创建索引
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_lifecycle ON memories(lifecycle)
        """)

        self.conn.commit()

    @property
    def vector_db(self):
        """延迟加载向量数据库"""
        if self._vector_db is None:
            from ..vector_db import ChromaVectorDB
            self._vector_db = ChromaVectorDB(persist_directory=self._vector_db_path)
        return self._vector_db

    def _rerank_by_slots(
        self,
        candidates: List[Dict[str, Any]],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> List[Dict[str, Any]]:
        """
        按槽位匹配度和欧氏距离重排序

        排序逻辑：
        1. 先按"查询槽位在记忆中对应的槽位数量"升序（匹配少的更精准，排前面）
        2. 再按平均欧氏距离升序

        Args:
            candidates: 候选结果列表，每项包含 memory_id, metadata
            query_slot_vectors: 查询的槽位向量

        Returns:
            重排序后的结果列表
        """
        reranked = []

        for cand in candidates:
            metadata = cand.get("metadata", {}) or {}

            match_count = 0
            total_distance = 0.0
            slot_distances = {}

            for slot_name, query_vec in query_slot_vectors.items():
                slot_key = f"_slot_{slot_name}"
                stored_slot_vec = metadata.get(slot_key)

                if stored_slot_vec is not None:
                    stored_vec = np.array(stored_slot_vec).flatten()
                    query_vec = np.array(query_vec).flatten()
                    dist = float(np.linalg.norm(query_vec - stored_vec))

                    match_count += 1
                    total_distance += dist
                    slot_distances[slot_name] = dist

            avg_distance = total_distance / match_count if match_count > 0 else float("inf")

            reranked.append({
                "memory_id": cand["memory_id"],
                "match_count": match_count,
                "avg_distance": avg_distance,
                "slot_distances": slot_distances,
                "metadata": metadata,
            })

        # 排序：1. match_count 升序，2. avg_distance 升序
        reranked.sort(key=lambda x: (x["match_count"], x["avg_distance"]))

        return reranked

    # =========================================================================
    # 写入
    # =========================================================================

    def write(
        self,
        query_sentence: str,
        content: str,
        lifecycle: int,
        embedding_mode: str = EmbeddingMode.INTEREST,
    ) -> str:
        """
        写入记忆

        Args:
            query_sentence: 查询句 "<时间><主体><动作><宾语><目的><结果>"
            content: 记忆内容
            lifecycle: 生命周期
            embedding_mode: 嵌入模式（写入向量数据库时使用）

        Returns:
            memory_id: 写入的记忆 ID
        """
        # 解析查询句为 QuerySlots
        slots = self._parse_query_sentence(query_sentence)

        # 生成两种嵌入向量
        # interest_vec: 经过自注意力处理（兴趣嵌入）
        # raw_vec: 直接拼接（原始嵌入）
        interest_vec = self.pipeline.encode(slots, use_raw=False, normalize=True)
        raw_vec = self.pipeline.encode(slots, use_raw=True, normalize=True)

        # 生成 memory_id
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"

        # 创建 Memory 对象
        memory = Memory(
            id=memory_id,
            query_sentence=query_sentence,
            query_embedding=interest_vec,
            raw_embedding=raw_vec,
            content=content,
            lifecycle=lifecycle,
        )

        # 存入 KV 数据库
        self._write_to_kv(memory)

        # 根据嵌入模式存入向量数据库
        if embedding_mode == EmbeddingMode.INTEREST:
            vec = interest_vec
        elif embedding_mode == EmbeddingMode.RAW:
            vec = raw_vec
        else:  # BOTH - 默认用 interest
            vec = interest_vec

        # 获取槽位向量并构建 metadata
        slot_vecs = self.pipeline.get_slot_vectors(slots)
        metadata = {"query_sentence": query_sentence}

        # 从 query_sentence 中提取槽位字符串值
        # 格式: <时间><主体><动作><宾语><目的><结果>
        parts = []
        current = ""
        inBracket = False
        for char in query_sentence:
            if char == "<":
                inBracket = True
                current = ""
            elif char == ">":
                inBracket = False
                parts.append(current)
            elif inBracket:
                current += char

        if len(parts) == 6:
            slot_names = ["scene", "subject", "action", "object", "purpose", "result"]
            for i, slot_name in enumerate(slot_names):
                metadata[slot_name] = parts[i]  # 存储槽位字符串值

        # 存储槽位向量
        for slot_name, slot_vec in slot_vecs.items():
            metadata[f"_slot_{slot_name}"] = slot_vec.tolist()

        self.vector_db.add(
            vector=vec,
            metadata=metadata,
            memory_id=memory_id,
        )

        return memory_id

    def _write_to_kv(self, memory: Memory):
        """写入 KV 数据库"""
        self.cursor.execute("""
            INSERT INTO memories (
                id, query_sentence, query_embedding, raw_embedding,
                content, lifecycle, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            memory.id,
            memory.query_sentence,
            memory.query_embedding.tobytes(),
            memory.raw_embedding.tobytes(),
            memory.content,
            memory.lifecycle,
            memory.created_at.isoformat(),
            memory.updated_at.isoformat(),
        ))
        self.conn.commit()

    def _parse_query_sentence(self, query_sentence: str) -> QuerySlots:
        """
        解析查询句为 QuerySlots

        Args:
            query_sentence: "<时间><主体><动作><宾语><目的><结果>"

        Returns:
            QuerySlots 对象
        """
        # 解析槽位值
        # 格式: <平时><我><学><编程><喜欢><有收获>
        parts = []
        current = ""
        inBracket = False

        for char in query_sentence:
            if char == "<":
                inBracket = True
                current = ""
            elif char == ">":
                inBracket = False
                parts.append(current)
            elif inBracket:
                current += char

        if len(parts) != 6:
            raise ValueError(f"查询句必须包含6个槽位，得到 {len(parts)} 个: {query_sentence}")

        scene_val, subject, action, obj, purpose, result = parts

        # 转换为 token_ids（使用简单的字符编码）
        def text_to_ids(text: str) -> torch.Tensor:
            """简单的文本到 token_ids 转换"""
            if not text:
                return torch.tensor([[0]], dtype=torch.long)
            # 使用 ord() 的前几位作为伪 token
            ids = torch.tensor([[ord(c) % 1000 for c in text]], dtype=torch.long)
            return ids

        return QuerySlots(
            scene=text_to_ids(scene_val),
            subject=text_to_ids(subject),
            action=text_to_ids(action),
            object=text_to_ids(obj),
            purpose=text_to_ids(purpose),
            result=text_to_ids(result),
        )

    # =========================================================================
    # 读取
    # =========================================================================

    def read(self, memory_id: str) -> Optional[Memory]:
        """
        读取单条记忆

        Args:
            memory_id: 记忆 ID

        Returns:
            Memory 对象，如果不存在返回 None
        """
        self.cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
        row = self.cursor.fetchone()

        if row is None:
            return None

        return Memory(
            id=row["id"],
            query_sentence=row["query_sentence"],
            query_embedding=np.frombuffer(row["query_embedding"], dtype=np.float32),
            raw_embedding=np.frombuffer(row["raw_embedding"], dtype=np.float32),
            content=row["content"],
            lifecycle=row["lifecycle"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # =========================================================================
    # 更新
    # =========================================================================

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        lifecycle: Optional[int] = None,
    ) -> bool:
        """
        更新记忆

        Args:
            memory_id: 记忆 ID
            content: 新内容（可选）
            lifecycle: 新生命周期（可选）

        Returns:
            是否更新成功
        """
        memory = self.read(memory_id)
        if memory is None:
            return False

        updates = []
        params = []

        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if lifecycle is not None:
            updates.append("lifecycle = ?")
            params.append(lifecycle)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(memory_id)

        sql = f"UPDATE memories SET {', '.join(updates)} WHERE id = ?"
        self.cursor.execute(sql, params)
        self.conn.commit()

        return self.cursor.rowcount > 0

    # =========================================================================
    # 删除
    # =========================================================================

    def delete(self, memory_id: str) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆 ID

        Returns:
            是否删除成功
        """
        # 从 KV 数据库删除
        self.cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()

        # 从向量数据库删除
        self.vector_db.delete(memory_id)

        return self.cursor.rowcount > 0

    # =========================================================================
    # 搜索
    # =========================================================================

    def search_fullspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        embedding_mode: str = EmbeddingMode.INTEREST,
        use_slot_rerank: bool = False,
    ) -> List[SearchResult]:
        """
        全空间向量相似度搜索

        Args:
            query_slots: 查询槽位，如 {"subject": "我", "action": "学"}
            top_k: 返回数量
            embedding_mode: 嵌入模式
            use_slot_rerank: 是否使用槽位匹配+距离排序（替代向量距离排序）
                            排序逻辑：先按匹配数量升序，再按平均欧氏距离升序

        Returns:
            List[SearchResult]，sort_by 字段表示排序依据
        """
        # 解析查询槽位
        slots = self._parse_query_slots(query_slots)

        # 生成查询向量
        if embedding_mode == EmbeddingMode.INTEREST:
            vec = self.pipeline.encode(slots, use_raw=False, normalize=True)
        else:
            vec = self.pipeline.encode(slots, use_raw=True, normalize=True)

        # 搜索向量数据库
        results = self.vector_db.search(query_vector=vec, top_k=top_k * 10 if use_slot_rerank else top_k)

        if not use_slot_rerank:
            # 不排序：直接按向量距离返回
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

        # 使用槽位排序：先按匹配数量降序，再按平均距离升序
        slot_vecs = self.pipeline.get_slot_vectors(slots)
        active_slots = {k: v for k, v in slot_vecs.items() if np.linalg.norm(v) > 0}

        # 构建候选集，计算 match_count 和 avg_distance
        candidates = []
        for r in results:
            item = self.vector_db.get(r["memory_id"])
            if not item:
                continue
            metadata = item.get("metadata", {})

            # 计算匹配数：槽位值与查询值完全一致的槽位数量
            match_count = 0
            for slot_name in active_slots.keys():
                query_value = query_slots.get(slot_name, "")
                result_value = metadata.get(slot_name, "")
                if query_value and result_value and query_value == result_value:
                    match_count += 1

            # 计算各槽位欧氏距离的平均值
            slot_dists = []
            for slot_name, query_vec in active_slots.items():
                slot_key = f"_slot_{slot_name}"
                stored_vec = metadata.get(slot_key)
                if stored_vec is not None:
                    dist = float(np.linalg.norm(query_vec.flatten() - np.array(stored_vec).flatten()))
                    slot_dists.append(dist)

            avg_distance = np.mean(slot_dists) if slot_dists else r["distance"]

            candidates.append({
                "memory_id": r["memory_id"],
                "distance": r["distance"],
                "metadata": metadata,
                "match_count": match_count,
                "avg_distance": avg_distance,
            })

        # 排序：先按 match_count 降序，再按 avg_distance 升序
        candidates.sort(key=lambda x: (-x["match_count"], x["avg_distance"]))

        # 构建返回结果
        search_results = []
        for r in candidates[:top_k]:
            sr = SearchResult(
                memory_id=r["memory_id"],
                distance=r["distance"],
                metadata=r.get("metadata", {}),
                sort_by="slot_match",
                match_count=r["match_count"],
                avg_distance=r["avg_distance"],
            )
            search_results.append(sr)

        return search_results

    def search_subspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        embedding_mode: str = EmbeddingMode.INTEREST,
        use_slot_rerank: bool = True,
    ) -> List[SearchResult]:
        """
        子空间向量相似度搜索

        在多个槽位的 64 维子空间内分别搜索，然后取交集。

        Args:
            query_slots: 查询槽位，如 {"subject": "我"} 或 {"subject": "我", "purpose": "锻炼身体"}
                         多个槽位时，会分别在每个槽位子空间搜索，然后取 memory_id 交集
            top_k: 返回数量
            embedding_mode: 嵌入模式
            use_slot_rerank: 是否使用槽位匹配+距离排序（替代向量距离排序）
                            排序逻辑：先按匹配数量升序，再按平均欧氏距离升序

        Returns:
            List[SearchResult]，sort_by 字段表示排序依据（"slot_match" 或 None）
        """
        from ..vector_db import SubspaceSearcher

        # 解析查询槽位
        slots = self._parse_query_slots(query_slots)

        # 获取各槽位向量
        slot_vecs = self.pipeline.get_slot_vectors(slots)

        # 收集所有有值的槽位
        active_slots = {}
        for slot_name in ["scene", "subject", "action", "object", "purpose", "result"]:
            if slot_name in slot_vecs and np.linalg.norm(slot_vecs[slot_name]) > 0:
                active_slots[slot_name] = slot_vecs[slot_name]

        if not active_slots:
            return []

        # 如果只有一个槽位，直接搜索
        if len(active_slots) == 1:
            slot_name = list(active_slots.keys())[0]
            slot_vec = list(active_slots.values())[0]
            return self._subspace_search_single(slot_name, slot_vec, top_k, use_slot_rerank)

        # 真正的子空间搜索：分别在每个槽位的 64 维子空间内搜索，取交集
        from ..embedding_coder import SLOT_NAMES, SLOT_DIM

        slot_ids = []  # 每个槽位搜索到的 memory_id 集合
        slot_dists = {}  # memory_id -> {slot_name: distance(64维)}

        for slot_name, slot_vec in active_slots.items():
            # 从向量数据库获取所有向量，在 64 维子空间内计算距离
            slot_idx = SLOT_NAMES.index(slot_name)
            query_subvec = slot_vec.flatten()  # [64]

            # 取出所有记录
            all_items = self.vector_db.get_all(include_embeddings=True)

            # 在 64 维子空间内计算距离
            results = []
            for item in all_items:
                embedding = item.get("embedding")
                if embedding is None:
                    continue
                embedding = np.array(embedding).flatten()  # [384]
                # 提取对应槽位的 64 维子向量
                stored_subvec = embedding[slot_idx * SLOT_DIM:(slot_idx + 1) * SLOT_DIM]
                # 计算子空间欧氏距离
                dist = float(np.linalg.norm(query_subvec - stored_subvec))
                results.append({
                    "memory_id": item["memory_id"],
                    "distance": dist,
                    "metadata": item.get("metadata", {}),
                })

            # 按距离排序，取 top_k * 10
            results.sort(key=lambda x: x["distance"])
            results = results[:top_k * 10]

            ids = set(r["memory_id"] for r in results)
            slot_ids.append(ids)

            for r in results:
                mem_id = r["memory_id"]
                if mem_id not in slot_dists:
                    slot_dists[mem_id] = {}
                slot_dists[mem_id][slot_name] = r["distance"]

        # 取交集
        common_ids = set.intersection(*slot_ids) if slot_ids else set()
        if not common_ids:
            return []

        # 构建结果：计算每个结果的匹配数（槽位值与查询值完全一致的个数）
        results_info = []
        for mem_id in common_ids:
            item = self.vector_db.get(mem_id)
            if not item:
                continue
            metadata = item.get("metadata", {})

            # 计算匹配数：槽位值与查询值完全一致的槽位数量
            match_count = 0
            for slot_name in active_slots.keys():
                query_value = query_slots.get(slot_name, "")
                result_value = metadata.get(slot_name, "")
                if query_value and result_value and query_value == result_value:
                    match_count += 1

            dists = [slot_dists[mem_id].get(s, float("inf")) for s in active_slots.keys()]
            results_info.append({
                "memory_id": mem_id,
                "avg_distance": np.mean(dists),
                "match_count": match_count,
                "metadata": metadata,
            })

        if not use_slot_rerank:
            # 不排序：直接按向量距离返回
            results_info.sort(key=lambda x: x["avg_distance"])
            search_results = []
            for info in results_info[:top_k]:
                sr = SearchResult(
                    memory_id=info["memory_id"],
                    distance=info["avg_distance"],
                    metadata=info["metadata"],
                    sort_by=None,
                )
                search_results.append(sr)
            return search_results

        # 排序：先按 match_count 降序，再按平均距离升序
        results_info.sort(key=lambda x: (-x["match_count"], x["avg_distance"]))

        search_results = []
        for info in results_info[:top_k]:
            sr = SearchResult(
                memory_id=info["memory_id"],
                distance=info["avg_distance"],
                metadata=info["metadata"],
                sort_by="slot_match",
                match_count=info["match_count"],
                avg_distance=info["avg_distance"],
            )
            search_results.append(sr)

        return search_results

    def _subspace_search_single(
        self,
        slot_name: str,
        slot_vec: np.ndarray,
        top_k: int,
        use_slot_rerank: bool,
    ) -> List[SearchResult]:
        """单槽位子空间搜索（在 64 维子空间内计算距离）"""
        from ..embedding_coder import SLOT_NAMES, SLOT_DIM

        slot_idx = SLOT_NAMES.index(slot_name)
        query_subvec = slot_vec.flatten()  # [64]

        # 取出所有记录
        all_items = self.vector_db.get_all(include_embeddings=True)

        # 在 64 维子空间内计算距离
        results = []
        for item in all_items:
            embedding = item.get("embedding")
            if embedding is None:
                continue
            embedding = np.array(embedding).flatten()  # [384]
            # 提取对应槽位的 64 维子向量
            stored_subvec = embedding[slot_idx * SLOT_DIM:(slot_idx + 1) * SLOT_DIM]
            # 计算子空间欧氏距离
            dist = float(np.linalg.norm(query_subvec - stored_subvec))
            results.append({
                "memory_id": item["memory_id"],
                "distance": dist,
                "metadata": item.get("metadata", {}),
            })

        # 按子空间距离排序
        results.sort(key=lambda x: x["distance"])

        if not use_slot_rerank:
            # 不排序：直接按子空间距离返回
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

        # 使用槽位排序：计算 match_count 和 avg_distance
        candidates = []
        for r in results:
            metadata = r.get("metadata", {})
            # match_count：当前槽位的 metadata 值是否与查询一致
            match_count = 0
            stored_val = metadata.get(slot_name, "")
            # 查询槽位值需要从外部传入，这里简化：单槽位匹配=1
            if stored_val:
                match_count = 1

            candidates.append({
                "memory_id": r["memory_id"],
                "distance": r["distance"],
                "metadata": metadata,
                "match_count": match_count,
                "avg_distance": r["distance"],
            })

        # 排序：先按 match_count 降序，再按 avg_distance 升序
        candidates.sort(key=lambda x: (-x["match_count"], x["avg_distance"]))

        search_results = []
        for r in candidates[:top_k]:
            sr = SearchResult(
                memory_id=r["memory_id"],
                distance=r["distance"],
                metadata=r.get("metadata", {}),
                sort_by="slot_match",
                match_count=r["match_count"],
                avg_distance=r["avg_distance"],
            )
            search_results.append(sr)

        return search_results

    def _parse_query_slots(self, query_slots: Dict[str, str]) -> QuerySlots:
        """
        解析查询槽位字典为 QuerySlots

        Args:
            query_slots: {"scene": "平时", "subject": "我", ...}

        Returns:
            QuerySlots 对象
        """
        def text_to_ids(text: str) -> torch.Tensor:
            if not text:
                return torch.tensor([[0]], dtype=torch.long)
            ids = torch.tensor([[ord(c) % 1000 for c in text]], dtype=torch.long)
            return ids

        slots_dict = {}
        for slot_name in ["scene", "subject", "action", "object", "purpose", "result"]:
            value = query_slots.get(slot_name)
            if value is not None:
                slots_dict[slot_name] = text_to_ids(value)

        # 构建 QuerySlots
        return QuerySlots(
            scene=slots_dict.get("scene"),
            subject=slots_dict.get("subject"),
            action=slots_dict.get("action"),
            object=slots_dict.get("object"),
            purpose=slots_dict.get("purpose"),
            result=slots_dict.get("result"),
        )

    # =========================================================================
    # 工具方法
    # =========================================================================

    def count(self) -> int:
        """返回记忆总数"""
        self.cursor.execute("SELECT COUNT(*) FROM memories")
        return self.cursor.fetchone()[0]

    def exists(self, memory_id: str) -> bool:
        """检查记忆是否存在"""
        self.cursor.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,))
        return self.cursor.fetchone() is not None

    def clear(self):
        """清空所有记忆（危险操作）"""
        # 清空 KV 数据库
        self.cursor.execute("DELETE FROM memories")
        self.conn.commit()

        # 清空向量数据库
        self.vector_db.clear()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
        if self._vector_db:
            self._vector_db.close()
            self._vector_db = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
