"""
星尘记忆系统 - PyAPI 应用层封装

整合所有组件（VecDBCRUD、Scheduler、TimeIndex、SlotIndex、
LifecycleManager），提供统一的 Python API。

功能：
- 创建/删除多个独立的记忆系统
- 记忆的增删查改（支持向量搜索、时间索引、生命周期查询）
- 记忆系统维护（过期删除、相似度过滤、兴趣模式开关）
"""

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

# TYPE_CHECKING 用于避免循环导入
if TYPE_CHECKING:
    from ..basic_crud.vec_db_crud import Memory

import numpy as np

from ..basic_crud import VecDBCRUD
from ..embedding_coder import InterestEncoder, QueryEncoderPipeline, QuerySlots
from ..auxiliary_query import Scheduler, TimeIndex, SlotIndex
from ..lifecycle_manager import LifecycleManager, LIFECYCLE_INFINITY
from ..mql import Interpreter as MQLInterpreter, QueryResult as MQLResult


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class Memory:
    """记忆数据模型"""
    id: str
    query_sentence: str           # "<时间><主体><动作><宾语><目的><结果>"
    query_embedding: np.ndarray    # 兴趣嵌入 [384]
    raw_embedding: np.ndarray      # 原始嵌入 [384]
    content: str                   # 记忆内容（可留空）
    lifecycle: int                 # 生命周期（秒）
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

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
        )


@dataclass
class SearchResult:
    """搜索结果（不含 Memory 内容）"""
    memory_id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)
    sort_by: Optional[str] = None   # "slot_match" 或 None
    match_count: int = 0            # 槽位值与查询值完全一致的个数
    avg_distance: float = 0.0        # 各槽位欧氏距离均值


@dataclass
class LifecycleQueryResult:
    """生命周期查询结果"""
    memory_id: str
    lifecycle: int
    is_expired: bool
    expires_at: Optional[datetime] = None


# ============================================================================
# 常量
# ============================================================================

# 嵌入模式
class EmbeddingMode:
    INTEREST = "interest"   # 兴趣嵌入（经过自注意力）
    RAW = "raw"             # 原始嵌入（不过自注意力）

# 相似度阈值
DEFAULT_SIMILARITY_THRESHOLD = 0.85  # 余弦相似度 > 0.85 视为相似


# ============================================================================
# MemorySystem - 单个记忆系统
# ============================================================================

class MemorySystem:
    """
    单个记忆系统

    每个记忆系统包含独立的数据库：
    - VecDBCRUD: 向量存储（6槽位 + 全量Collection）
    - Scheduler: 数据库调度器（管理 KV 和 SQL 数据库）
    - TimeIndex: 时间索引
    - SlotIndex: 槽位索引
    - LifecycleManager: 生命周期管理器
    """

    def __init__(
        self,
        name: str,
        persist_directory: str,
        vocab_size: int = 10000,
        enable_interest_mode: bool = True,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ):
        """
        初始化 MemorySystem（延迟初始化，调用 initialize() 完成初始化）

        Args:
            name: 记忆系统名称
            persist_directory: 持久化根目录
            vocab_size: InterestEncoder 词汇表大小
            enable_interest_mode: 是否启用兴趣记忆模式（False 则跳过 InterestEncoder 相关计算）
            similarity_threshold: 相似度阈值，超过此值视为相似（仅在 interest_mode 下生效）
        """
        self.name = name
        self.persist_directory = Path(persist_directory) / name
        self.vocab_size = vocab_size
        self.enable_interest_mode = enable_interest_mode
        self.similarity_threshold = similarity_threshold

        # 各数据库路径
        self.vec_db_path = str(self.persist_directory / "vec_db")
        self.aux_db_path = str(self.persist_directory / "aux_db")

        # 内部组件（延迟初始化）
        self._vec_db: Optional[VecDBCRUD] = None
        self._scheduler: Optional[Scheduler] = None
        self._time_index: Optional[TimeIndex] = None
        self._slot_index: Optional[SlotIndex] = None
        self._lifecycle_mgr: Optional[LifecycleManager] = None
        self._initialized = False

    # =========================================================================
    # 初始化 / 关闭
    # =========================================================================

    def initialize(self):
        """初始化所有数据库组件"""
        if self._initialized:
            return

        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # VecDBCRUD
        self._vec_db = VecDBCRUD(
            persist_directory=self.vec_db_path,
            vocab_size=self.vocab_size,
        )

        # Scheduler + TimeIndex + SlotIndex
        self._scheduler = Scheduler(base_directory=self.aux_db_path)
        sql_db = self._scheduler.create_sql("index_db")

        self._time_index = TimeIndex(sql_db=sql_db)
        self._slot_index = SlotIndex(
            chroma_path=str(self.persist_directory / "slot_chroma"),
            sql_db=sql_db,
            slot_dim=64,
        )

        # LifecycleManager（复用 MemorySystem 的 enable_interest_mode）
        self._lifecycle_mgr = LifecycleManager(
            vec_db=self._vec_db,
            sql_db=sql_db,
            top_k=20,
            sample_size=5,
            enable_interest_mode=self.enable_interest_mode,
        )

        self._initialized = True

    def close(self):
        """关闭所有数据库连接"""
        if self._vec_db:
            self._vec_db.close()
            self._vec_db = None

        if self._scheduler:
            self._scheduler.close()
            self._scheduler = None

        self._time_index = None
        self._slot_index = None
        self._lifecycle_mgr = None
        self._initialized = False

    def __enter__(self):
        if not self._initialized:
            self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # =========================================================================
    # 内部工具
    # =========================================================================

    def _check_initialized(self):
        if not self._initialized:
            raise RuntimeError(f"MemorySystem '{self.name}' 未初始化，请先调用 initialize()")

    def _parse_query_sentence(self, query_sentence: str) -> List[str]:
        """解析查询句为槽位列表"""
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

    def _build_query_sentence(
        self,
        time_word: str = None,
        subject: str = None,
        action: str = None,
        object: str = None,
        purpose: str = None,
        result: str = None,
    ) -> str:
        """构建查询句"""
        return f"<{time_word or ''}><{subject or ''}><{action or ''}><{object or ''}><{purpose or ''}><{result or ''}>"

    def _extract_time_word(self, query_sentence: str) -> str:
        """从查询句提取时间词"""
        parts = self._parse_query_sentence(query_sentence)
        return parts[0] if parts else ""

    # =========================================================================
    # CRUD - 写入
    # =========================================================================

    def write(
        self,
        query_sentence: str,
        content: str = "",
        lifecycle: int = 86400,
        time_word: str = None,
        created_at: datetime = None,
    ) -> str:
        """
        写入记忆

        Args:
            query_sentence: 查询句 "<时间><主体><动作><宾语><目的><结果>"
            content: 记忆内容（可留空）
            lifecycle: 生命周期（秒），默认 1 天
            time_word: 时间词（从查询句自动提取，可覆盖）
            created_at: 创建时间（默认当前时间）

        Returns:
            memory_id

        Raises:
            ValueError: 查询句格式错误或与现有记忆过于相似
        """
        self._check_initialized()

        # 构建查询句（如果只传了槽位参数）
        if "<" not in query_sentence:
            query_sentence = self._build_query_sentence(
                time_word=time_word,
                subject=query_sentence,  # 第一个参数当作 subject
            )

        # 解析槽位
        parts = self._parse_query_sentence(query_sentence)
        time_word_extracted = parts[0] or time_word or ""
        created_at = created_at or datetime.now()

        # 相似度检查（仅在兴趣模式下）
        if self.enable_interest_mode and self.similarity_threshold > 0:
            if self._is_similar(query_sentence):
                raise ValueError("写入被拒绝：与现有记忆过于相似")

        # 构建槽位字典（用于 LifecycleManager）
        slot_dict = {
            "time": parts[0],
            "subject": parts[1],
            "action": parts[2],
            "object": parts[3],
            "purpose": parts[4],
            "result": parts[5],
        }
        # 过滤 None 值
        slot_dict = {k: v for k, v in slot_dict.items() if v}

        # 如果有 LifecycleManager（无论是否启用兴趣模式），用它决定生命周期
        if self._lifecycle_mgr is not None:
            lifecycle = self._lifecycle_mgr.decide_new_lifecycle(
                query_slots=slot_dict,
                reference_duration=lifecycle,
            )

        # 写入 VecDBCRUD
        embedding_mode = EmbeddingMode.INTEREST if self.enable_interest_mode else EmbeddingMode.RAW
        memory_id = self._vec_db.write(
            query_sentence=query_sentence,
            content=content,
            lifecycle=lifecycle,
            embedding_mode=embedding_mode,
        )

        # 写入时间索引
        if time_word_extracted:
            self._time_index.add(
                memory_id=memory_id,
                time_word=time_word_extracted,
                created_at=created_at,
            )

        # 写入槽位索引（使用原始向量）
        slots = self._vec_db._slots_from_sentence(query_sentence)
        slot_vecs = self._vec_db.pipeline.get_slot_vectors(slots)
        slot_values = {name: parts[i] for i, name in enumerate(self._vec_db.SLOT_NAMES)}
        self._slot_index.add(
            memory_id=memory_id,
            slot_vectors={k: v for k, v in slot_vecs.items() if k in slot_values},
            slot_values=slot_values,
        )

        return memory_id

    # =========================================================================
    # CRUD - 读取
    # =========================================================================

    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """
        根据 memory_id 获取记忆内容

        Args:
            memory_id: 记忆 ID

        Returns:
            Memory 对象，不存在返回 None
        """
        self._check_initialized()
        return self._vec_db._kv_read(memory_id)

    def get_memories(self, memory_ids: List[str]) -> Dict[str, Memory]:
        """
        批量获取记忆

        Args:
            memory_ids: 记忆 ID 列表

        Returns:
            {memory_id: Memory} 字典
        """
        self._check_initialized()
        result = {}
        for mid in memory_ids:
            mem = self._vec_db._kv_read(mid)
            if mem:
                result[mid] = mem
        return result

    # =========================================================================
    # CRUD - 更新
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
            content: 新内容（不修改则传 None）
            lifecycle: 新生命周期（不修改则传 None）

        Returns:
            是否成功
        """
        self._check_initialized()
        return self._vec_db.update(memory_id=memory_id, content=content, lifecycle=lifecycle)

    # =========================================================================
    # CRUD - 删除
    # =========================================================================

    def delete(self, memory_id: str) -> bool:
        """
        删除单个记忆

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        self._check_initialized()

        # 从 VecDBCRUD 删除
        ok = self._vec_db.delete(memory_id)

        # 从时间索引删除
        self._time_index.remove(memory_id)

        # 从槽位索引删除
        self._slot_index.remove(memory_id)

        return ok

    def delete_many(self, memory_ids: List[str]) -> int:
        """
        批量删除记忆

        Args:
            memory_ids: 记忆 ID 列表

        Returns:
            成功删除的数量
        """
        self._check_initialized()
        count = 0
        for mid in memory_ids:
            if self.delete(mid):
                count += 1
        return count

    # =========================================================================
    # 搜索 - 向量搜索
    # =========================================================================

    def search_subspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        use_slot_rerank: bool = True,
    ) -> List[SearchResult]:
        """
        子空间搜索：在每个槽位独立 Collection 中搜索，取交集后排序

        Args:
            query_slots: {"subject": "我", "action": "学习", ...}
            top_k: 返回数量
            use_slot_rerank: 是否用槽位匹配+距离排序

        Returns:
            SearchResult 列表
        """
        self._check_initialized()
        return self._vec_db.search_subspace(
            query_slots=query_slots,
            top_k=top_k,
            use_slot_rerank=use_slot_rerank,
        )

    def search_fullspace(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        embedding_mode: str = EmbeddingMode.INTEREST,
        use_slot_rerank: bool = False,
    ) -> List[SearchResult]:
        """
        全空间搜索：在 full_vectors Collection（384维）中搜索

        Args:
            query_slots: {"subject": "我", "action": "学习", ...}
            top_k: 返回数量
            embedding_mode: INTEREST 或 RAW
            use_slot_rerank: 是否用槽位匹配+距离排序

        Returns:
            SearchResult 列表
        """
        self._check_initialized()

        # 如果禁用了兴趣模式，强制使用 RAW
        if not self.enable_interest_mode:
            embedding_mode = EmbeddingMode.RAW

        return self._vec_db.search_fullspace(
            query_slots=query_slots,
            top_k=top_k,
            embedding_mode=embedding_mode,
            use_slot_rerank=use_slot_rerank,
        )

    def search(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        use_subspace: bool = True,
    ) -> List[SearchResult]:
        """
        统一搜索接口（自动选择子空间或全空间）

        Args:
            query_slots: 查询槽位
            top_k: 返回数量
            use_subspace: True=子空间搜索，False=全空间搜索

        Returns:
            SearchResult 列表
        """
        if use_subspace:
            return self.search_subspace(query_slots, top_k=top_k)
        else:
            return self.search_fullspace(query_slots, top_k=top_k)

    # =========================================================================
    # 搜索 - 时间索引查询
    # =========================================================================

    def search_by_time_range(
        self,
        start: datetime,
        end: datetime,
        top_k: int = 100,
    ) -> List[str]:
        """
        按时间范围查询

        Args:
            start: 开始时间
            end: 结束时间
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        ids = self._time_index.query_by_range(start, end)
        return ids[:top_k]

    def search_by_time_words(
        self,
        time_words: List[str],
        fuzzy: bool = True,
        top_k: int = 100,
    ) -> List[str]:
        """
        按时间词查询

        Args:
            time_words: 时间词列表，如["平时", "经常"]
            fuzzy: 是否启用模糊匹配
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        ids = self._time_index.query_by_words(time_words, fuzzy=fuzzy)
        return ids[:top_k]

    def search_recent(
        self,
        days: int = 7,
        top_k: int = 100,
    ) -> List[str]:
        """
        查询最近 N 天的记忆

        Args:
            days: 天数
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        ids = self._time_index.query_recent(days=days)
        return ids[:top_k]

    # =========================================================================
    # 搜索 - 槽位索引查询
    # =========================================================================

    def search_by_slot_value(
        self,
        word: str,
        slot: str,
        top_k: int = 10,
    ) -> List[str]:
        """
        按槽位值精确查找

        Args:
            word: 要查找的词
            slot: 槽位名（time/subject/action/object/purpose/result）
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        results = self._slot_index.find_by_word(word, slot, top_k=top_k)
        return [mid for mid, _ in results]

    def search_by_slots(
        self,
        slots: Dict[str, str],
        top_k: int = 10,
    ) -> List[str]:
        """
        按多个槽位值查找（交集）

        Args:
            slots: {slot_name: word, ...}
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        id_sets = []
        for slot, word in slots.items():
            results = self._slot_index.find_by_word(word, slot, top_k=top_k * 2)
            if not results:
                return []
            id_sets.append(set(mid for mid, _ in results))

        # 取交集
        common_ids = set.intersection(*id_sets) if id_sets else set()
        if not common_ids:
            return []

        return list(common_ids)[:top_k]

    # =========================================================================
    # 搜索 - 生命周期查询
    # =========================================================================

    def search_by_lifecycle(
        self,
        min_lifecycle: int = None,
        max_lifecycle: int = None,
        include_expired: bool = True,
        top_k: int = 100,
    ) -> List[LifecycleQueryResult]:
        """
        按生命周期范围查询

        Args:
            min_lifecycle: 最小生命周期（秒）
            max_lifecycle: 最大生命周期（秒）
            include_expired: 是否包含已过期的记忆
            top_k: 最大返回数量

        Returns:
            LifecycleQueryResult 列表
        """
        self._check_initialized()
        all_memories = self._vec_db._kv_read_all()

        results = []
        for mem in all_memories:
            if min_lifecycle is not None and mem.lifecycle < min_lifecycle:
                continue
            if max_lifecycle is not None and mem.lifecycle > max_lifecycle:
                continue

            if not include_expired:
                if self._lifecycle_mgr.is_expired(mem.id):
                    continue

            expires_at = None
            if mem.lifecycle < LIFECYCLE_INFINITY:
                expires_at = mem.created_at + timedelta(seconds=mem.lifecycle)

            results.append(LifecycleQueryResult(
                memory_id=mem.id,
                lifecycle=mem.lifecycle,
                is_expired=self._lifecycle_mgr.is_expired(mem.id),
                expires_at=expires_at,
            ))

        # 按 lifecycle 排序
        results.sort(key=lambda x: x.lifecycle)
        return results[:top_k]

    def search_infinite_lifecycle(self, top_k: int = 100) -> List[str]:
        """
        查询永不过期的记忆

        Args:
            top_k: 最大返回数量

        Returns:
            memory_id 列表
        """
        self._check_initialized()
        all_memories = self._vec_db._kv_read_all()

        results = []
        for mem in all_memories:
            if mem.lifecycle >= LIFECYCLE_INFINITY:
                results.append(mem.id)

        return results[:top_k]

    # =========================================================================
    # 记忆系统维护
    # =========================================================================

    def delete_expired(self, dry_run: bool = False) -> List[str]:
        """
        删除所有过期记忆

        Args:
            dry_run: True=只返回待删除列表，不实际删除

        Returns:
            已删除（或待删除）的 memory_id 列表
        """
        self._check_initialized()

        all_ids = self.list_all_memory_ids()
        expired_ids = self._lifecycle_mgr.filter_expired(all_ids)

        if dry_run:
            return expired_ids

        deleted = []
        for mid in expired_ids:
            if self.delete(mid):
                deleted.append(mid)

        return deleted

    def cleanup_expired(self, batch_size: int = 100) -> Dict[str, int]:
        """
        批量清理过期记忆（分批处理）

        Args:
            batch_size: 每批处理的数量

        Returns:
            {"total": 总数, "expired": 已过期数, "deleted": 已删除数, "remaining": 剩余数}
        """
        self._check_initialized()
        return self._lifecycle_mgr.check_and_cleanup_all(batch_size=batch_size)

    def _is_similar(self, query_sentence: str) -> bool:
        """
        检查查询句是否与现有记忆过于相似

        Args:
            query_sentence: 查询句

        Returns:
            True=过于相似，False=可以写入
        """
        if not self.enable_interest_mode:
            return False

        parts = self._parse_query_sentence(query_sentence)
        slot_dict = {
            "time": parts[0],
            "subject": parts[1],
            "action": parts[2],
            "object": parts[3],
            "purpose": parts[4],
            "result": parts[5],
        }
        # 过滤 None 值
        slot_dict = {k: v for k, v in slot_dict.items() if v}

        if not slot_dict:
            return False

        # 全空间搜索 top_k
        results = self._vec_db.search_fullspace(
            query_slots=slot_dict,
            top_k=5,
            embedding_mode=EmbeddingMode.INTEREST,
            use_slot_rerank=False,
        )

        if not results:
            return False

        # 转换为余弦相似度
        top_result = results[0]
        # Chroma L2 距离转换为相似度（假设向量已归一化）
        # cosine_similarity ≈ 1 - l2_distance / 2
        max_distance = 2.0  # L2 距离最大值
        similarity = 1.0 - (top_result.distance / max_distance)
        similarity = max(0.0, min(1.0, similarity))

        return similarity >= self.similarity_threshold

    def get_similarity(self, query_sentence: str, memory_id: str) -> float:
        """
        获取查询句与指定记忆的相似度

        Args:
            query_sentence: 查询句
            memory_id: 记忆 ID

        Returns:
            相似度分数 [0, 1]
        """
        parts = self._parse_query_sentence(query_sentence)
        slot_dict = {
            "time": parts[0],
            "subject": parts[1],
            "action": parts[2],
            "object": parts[3],
            "purpose": parts[4],
            "result": parts[5],
        }
        slot_dict = {k: v for k, v in slot_dict.items() if v}

        if not slot_dict:
            return 0.0

        # 获取记忆的查询句
        memory = self._vec_db._kv_read(memory_id)
        if memory is None:
            return 0.0

        # 获取记忆的槽位
        mem_parts = self._parse_query_sentence(memory.query_sentence)
        mem_slots = {
            "time": mem_parts[0],
            "subject": mem_parts[1],
            "action": mem_parts[2],
            "object": mem_parts[3],
            "purpose": mem_parts[4],
            "result": mem_parts[5],
        }
        mem_slots = {k: v for k, v in mem_slots.items() if v}

        # 计算重叠的槽位
        common_slots = set(slot_dict.keys()) & set(mem_slots.keys())
        if not common_slots:
            return 0.0

        # 计算字符串匹配比例
        matches = sum(1 for s in common_slots if slot_dict.get(s) == mem_slots.get(s))
        return matches / len(common_slots) if common_slots else 0.0

    # =========================================================================
    # 访问触发
    # =========================================================================

    def on_memory_accessed(self, memory_id: str) -> List[Tuple[str, int, int]]:
        """
        当记忆被访问时，触发生命周期更新

        以被访问记忆为中心进行被动回忆，更新相关记忆的生命周期。

        Args:
            memory_id: 被访问的记忆 ID

        Returns:
            [(memory_id, old_lifecycle, new_lifecycle), ...] 更新详情
        """
        self._check_initialized()
        if self._lifecycle_mgr is None:
            return []
        return self._lifecycle_mgr.on_memory_accessed(memory_id)

    # =========================================================================
    # 工具方法
    # =========================================================================

    def count(self) -> int:
        """返回记忆总数"""
        self._check_initialized()
        return self._vec_db.count()

    def exists(self, memory_id: str) -> bool:
        """检查记忆是否存在"""
        self._check_initialized()
        return self._vec_db.exists(memory_id)

    def list_all_memory_ids(self) -> List[str]:
        """列出所有记忆 ID"""
        self._check_initialized()
        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT id FROM memories")
        return [row["id"] for row in cur.fetchall()]

    def clear(self):
        """清空所有数据"""
        self._check_initialized()
        self._vec_db.clear()
        self._time_index.clear()
        self._slot_index.clear()

    def get_stats(self) -> Dict[str, Any]:
        """
        获取记忆系统统计信息

        Returns:
            统计信息字典
        """
        self._check_initialized()

        all_ids = self.list_all_memory_ids()
        total = len(all_ids)

        expired_count = len(self._lifecycle_mgr.filter_expired(all_ids))

        infinite_count = 0
        lifecycle_dist = {"<1h": 0, "1h-1d": 0, "1d-7d": 0, "7d-30d": 0, ">30d": 0, "infinite": 0}
        now = datetime.now()

        for mem_id in all_ids:
            mem = self._vec_db._kv_read(mem_id)
            if mem:
                if mem.lifecycle >= LIFECYCLE_INFINITY:
                    infinite_count += 1
                    lifecycle_dist["infinite"] += 1
                else:
                    if mem.lifecycle < 3600:
                        lifecycle_dist["<1h"] += 1
                    elif mem.lifecycle < 86400:
                        lifecycle_dist["1h-1d"] += 1
                    elif mem.lifecycle < 7 * 86400:
                        lifecycle_dist["1d-7d"] += 1
                    elif mem.lifecycle < 30 * 86400:
                        lifecycle_dist["7d-30d"] += 1
                    else:
                        lifecycle_dist[">30d"] += 1

        return {
            "name": self.name,
            "total": total,
            "expired": expired_count,
            "alive": total - expired_count,
            "infinite": infinite_count,
            "lifecycle_distribution": lifecycle_dist,
            "enable_interest_mode": self.enable_interest_mode,
            "similarity_threshold": self.similarity_threshold,
        }

    def __repr__(self) -> str:
        return f"MemorySystem(name={self.name}, initialized={self._initialized}, interest_mode={self.enable_interest_mode})"

    # =========================================================================
    # MQL - Memory Query Language
    # =========================================================================

    def execute(self, sql: str) -> MQLResult:
        """
        执行 MQL 语句

        MQL 是类 SQL 的记忆查询语言，支持：
        - SELECT ... FROM memories WHERE ... [VERSION v1] [LIMIT n]
        - INSERT INTO memories VALUES (...)
        - UPDATE memories SET ... WHERE ...
        - DELETE FROM memories WHERE ...
        - 向量搜索：SELECT * FROM memories WHERE [slot=value,...] SEARCH TOPK n

        Args:
            sql: MQL 语句

        Returns:
            MQLResult: 包含 data, affected_rows, memory_ids 等

        示例：
            result = system.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")
            for row in result.data:
                print(row)

            # 向量搜索
            result = system.execute(
                "SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 5"
            )

            # 插入
            system.execute(
                "INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)"
            )

            # 更新
            system.execute("UPDATE memories SET content='新内容' WHERE id='mem_xxx'")

            # 删除
            system.execute("DELETE FROM memories WHERE subject='我'")
        """
        self._check_initialized()

        # 创建解释器并绑定当前记忆系统
        interpreter = MQLInterpreter()
        interpreter.bind("mem", self)
        return interpreter.execute(sql)


# 解决循环引用：为 VecDBCRUD 添加 _kv_read_all 方法
def _extend_vec_db_crud():
    """为 VecDBCRUD 添加批量读取方法"""
    from ..basic_crud.vec_db_crud import VecDBCRUD, Memory

    if not hasattr(VecDBCRUD, '_kv_read_all'):
        def _kv_read_all(self) -> List:
            """读取所有记忆"""
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM memories")
            rows = cur.fetchall()
            memories = []
            for row in rows:
                memories.append(Memory(
                    id=row["id"],
                    query_sentence=row["query_sentence"],
                    query_embedding=np.frombuffer(row["query_embedding"], dtype=np.float32).copy(),
                    raw_embedding=np.frombuffer(row["raw_embedding"], dtype=np.float32).copy(),
                    content=row["content"],
                    lifecycle=row["lifecycle"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                ))
            return memories

        VecDBCRUD._kv_read_all = _kv_read_all


# 在模块加载时自动扩展
_extend_vec_db_crud()


# ============================================================================
# PyAPI - 多记忆系统管理
# ============================================================================

class PyAPI:
    """
    星尘记忆系统应用层封装（支持多记忆系统）

    核心概念：
    - MemorySystem: 单个独立的记忆系统，包含完整的数据存储
    - PyAPI: 管理多个 MemorySystem，提供统一的访问接口

    使用流程：
    1. 创建 PyAPI 实例
    2. create_system() 创建新的记忆系统
    3. get_system() 获取已有记忆系统
    4. 系统.write() / 系统.search() 操作单个记忆系统
    """

    def __init__(
        self,
        persist_directory: str = "./data/xcmemory",
        vocab_size: int = 10000,
    ):
        """
        初始化 PyAPI

        Args:
            persist_directory: 持久化根目录
            vocab_size: InterestEncoder 词汇表大小
        """
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.vocab_size = vocab_size

        # 管理的记忆系统: system_name -> MemorySystem
        self._systems: Dict[str, MemorySystem] = {}

        # 当前活跃的记忆系统
        self._active_system: Optional[str] = None

        # 元数据路径
        self._meta_path = self.persist_directory / "systems_meta.json"
        self._load_meta()

    # =========================================================================
    # 元数据管理
    # =========================================================================

    def _load_meta(self):
        """加载系统元数据"""
        import json
        if self._meta_path.exists():
            try:
                with open(self._meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    self._systems_meta = meta.get("systems", {})
            except Exception:
                self._systems_meta = {}
        else:
            self._systems_meta = {}

    def _save_meta(self):
        """保存系统元数据"""
        import json
        meta = {"systems": self._systems_meta}
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _get_system_meta(self, name: str) -> Dict[str, Any]:
        """获取系统元数据"""
        return self._systems_meta.get(name, {})

    def _set_system_meta(self, name: str, meta: Dict[str, Any]):
        """设置系统元数据"""
        self._systems_meta[name] = meta
        self._save_meta()

    # =========================================================================
    # 记忆系统管理 API
    # =========================================================================

    def create_system(
        self,
        name: str,
        enable_interest_mode: bool = False,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        initialize: bool = True,
    ) -> MemorySystem:
        """
        创建新的记忆系统

        Args:
            name: 记忆系统名称
            enable_interest_mode: 是否启用兴趣记忆模式（当前版本不支持，设为 True 会报错）
            similarity_threshold: 相似度阈值
            initialize: 是否立即初始化

        Returns:
            MemorySystem 实例

        Raises:
            ValueError: 如果系统已存在
            NotImplementedError: 如果 enable_interest_mode=True（当前版本不支持）
        """
        if name in self._systems:
            raise ValueError(f"记忆系统 '{name}' 已存在")

        # 当前版本不支持兴趣模型
        if enable_interest_mode:
            raise NotImplementedError(
                "兴趣模型模式尚未支持，请使用 enable_interest_mode=False 创建记忆系统。"
                "如需使用兴趣模型，请等待后续版本。"
            )

        system = MemorySystem(
            name=name,
            persist_directory=str(self.persist_directory),
            vocab_size=self.vocab_size,
            enable_interest_mode=enable_interest_mode,
            similarity_threshold=similarity_threshold,
        )

        if initialize:
            system.initialize()

        self._systems[name] = system
        self._active_system = name

        # 保存元数据
        self._set_system_meta(name, {
            "enable_interest_mode": enable_interest_mode,
            "similarity_threshold": similarity_threshold,
            "created_at": datetime.now().isoformat(),
        })

        return system

    def get_system(self, name: str) -> Optional[MemorySystem]:
        """
        获取已创建的记忆系统

        Args:
            name: 记忆系统名称

        Returns:
            MemorySystem 实例，不存在返回 None
        """
        return self._systems.get(name)

    def get_or_create_system(
        self,
        name: str,
        enable_interest_mode: bool = True,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> MemorySystem:
        """
        获取已有记忆系统，不存在则创建

        Args:
            name: 记忆系统名称
            enable_interest_mode: 是否启用兴趣记忆模式（仅创建时生效）
            similarity_threshold: 相似度阈值（仅创建时生效）

        Returns:
            MemorySystem 实例
        """
        if name in self._systems:
            return self._systems[name]

        return self.create_system(
            name=name,
            enable_interest_mode=enable_interest_mode,
            similarity_threshold=similarity_threshold,
        )

    def delete_system(self, name: str) -> bool:
        """
        删除记忆系统（危险操作）

        Args:
            name: 记忆系统名称

        Returns:
            是否成功删除
        """
        if name not in self._systems:
            return False

        # 关闭并删除
        system = self._systems[name]
        system.close()

        # 删除持久化数据
        system_dir = system.persist_directory
        if system_dir.exists():
            try:
                shutil.rmtree(system_dir)
            except PermissionError:
                # Windows 文件锁问题，等待后重试
                import time
                time.sleep(0.5)
                try:
                    shutil.rmtree(system_dir, ignore_errors=True)
                except Exception:
                    pass

        del self._systems[name]

        # 更新元数据
        if name in self._systems_meta:
            del self._systems_meta[name]
            self._save_meta()

        # 更新活跃系统
        if self._active_system == name:
            self._active_system = next(iter(self._systems.keys()), None)

        return True

    def list_systems(self) -> List[str]:
        """
        列出所有已创建的记忆系统名称

        Returns:
            系统名称列表
        """
        return list(self._systems.keys())

    def list_all_systems(self) -> List[Dict[str, Any]]:
        """
        列出所有记忆系统的详细信息

        Returns:
            [{"name": xxx, "enable_interest_mode": xxx, "created_at": xxx, ...}, ...]
        """
        return [
            {
                "name": name,
                **self._get_system_meta(name),
            }
            for name in self._systems.keys()
        ]

    def set_active_system(self, name: str):
        """
        设置当前活跃的记忆系统

        Args:
            name: 系统名称

        Raises:
            ValueError: 系统不存在
        """
        if name not in self._systems:
            raise ValueError(f"记忆系统 '{name}' 不存在")
        self._active_system = name

    @property
    def active_system(self) -> Optional[MemorySystem]:
        """获取当前活跃的记忆系统"""
        if self._active_system is None:
            return None
        return self._systems.get(self._active_system)

    @property
    def active_system_name(self) -> Optional[str]:
        """获取当前活跃的记忆系统名称"""
        return self._active_system

    # =========================================================================
    # 便捷方法（代理到当前活跃系统）
    # =========================================================================

    def write(self, *args, **kwargs) -> str:
        """写入记忆（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统，请先 create_system() 或 set_active_system()")
        return self.active_system.write(*args, **kwargs)

    def search_subspace(self, *args, **kwargs) -> List[SearchResult]:
        """子空间搜索（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.search_subspace(*args, **kwargs)

    def search_fullspace(self, *args, **kwargs) -> List[SearchResult]:
        """全空间搜索（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.search_fullspace(*args, **kwargs)

    def search(self, *args, **kwargs) -> List[SearchResult]:
        """统一搜索（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.search(*args, **kwargs)

    def get_memory(self, *args, **kwargs) -> Optional[Memory]:
        """获取记忆内容（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.get_memory(*args, **kwargs)

    def update(self, *args, **kwargs) -> bool:
        """更新记忆（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.update(*args, **kwargs)

    def delete(self, *args, **kwargs) -> bool:
        """删除记忆（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.delete(*args, **kwargs)

    def count(self) -> int:
        """返回记忆总数（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.count()

    def delete_expired(self, *args, **kwargs) -> List[str]:
        """删除过期记忆（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.delete_expired(*args, **kwargs)

    def get_stats(self, *args, **kwargs) -> Dict[str, Any]:
        """获取统计信息（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.get_stats(*args, **kwargs)

    def on_memory_accessed(self, *args, **kwargs) -> List[Tuple[str, int, int]]:
        """访问触发（代理到当前活跃系统）"""
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")
        return self.active_system.on_memory_accessed(*args, **kwargs)

    # =========================================================================
    # 关闭
    # =========================================================================

    def close(self):
        """关闭所有记忆系统"""
        for system in self._systems.values():
            system.close()
        self._systems.clear()
        self._active_system = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self) -> str:
        return f"PyAPI(systems={list(self._systems.keys())}, active={self._active_system})"

    # =========================================================================
    # MQL - Memory Query Language
    # =========================================================================

    def execute(self, sql: str) -> MQLResult:
        """
        执行 MQL 语句（代理到当前活跃系统）

        详见 MemorySystem.execute()
        """
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统，请先 create_system() 或 set_active_system()")
        return self.active_system.execute(sql)

    def execute_all(self, script: str) -> List[MQLResult]:
        """
        执行多行 MQL 脚本（分号分隔）

        Args:
            script: 多行 MQL 脚本

        Returns:
            各语句的结果列表
        """
        if self.active_system is None:
            raise RuntimeError("没有活跃的记忆系统")

        interpreter = MQLInterpreter()
        interpreter.bind("mem", self.active_system)
        return interpreter.execute_script(script)