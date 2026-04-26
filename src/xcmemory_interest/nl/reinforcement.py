"""
去重与记忆强化（Reinforcement）机制

参考: MEMU_TEXT2MEM_REFERENCE.md 第一章

功能:
- compute_content_hash: 生成记忆去重的唯一哈希值
- ReinforcementMixIn: 为 MemorySystem 增加强化追踪能力
- Salience Ranking: 向量检索时考虑强化因子 (similarity × reinforcement_count × recency_decay)

强化追踪字段（存储在 extra 字段）:
- content_hash: 内容哈希（精确去重）
- reinforcement_count: 强化次数
- last_reinforced_at: ISO 时间戳
"""

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..basic_crud.vec_db_crud import Memory as VecDBMemory
    from ..pyapi.core import MemorySystem, Memory

if TYPE_CHECKING:
    from ..pyapi.core import MemorySystem, Memory


# ============================================================================
# 内容哈希计算
# ============================================================================

def compute_content_hash(summary: str, memory_type: str = "memory") -> str:
    """
    生成记忆去重的唯一哈希值。

    归一化规则:
    - 小写化
    - 去除首尾空白
    - 合并多余空格

    Args:
        summary: 记忆摘要/内容文本
        memory_type: 记忆类型（用于区分不同类型的记忆去重）

    Returns:
        16 位十六进制哈希字符串
    """
    # 归一化: 小写化、去除首尾空白、合并多余空格
    normalized = " ".join(summary.lower().split())
    # 拼接类型和内容
    content = f"{memory_type}:{normalized}"
    # SHA256 哈希并取前 16 位
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ============================================================================
# Salience 分数计算
# ============================================================================

def compute_recency_decay(
    last_reinforced_at: Optional[str | datetime],
    created_at: Optional[str | datetime],
    half_life_days: float = 7.0,
) -> float:
    """
    计算时间衰减因子。

    使用指数衰减: decay = 0.5 ^ (days_since / half_life)
    其中 half_life 默认 7 天，即 7 天后权重减半。

    Args:
        last_reinforced_at: 最后强化时间的 ISO 格式字符串或 datetime 对象
        created_at: 创建时间的 ISO 格式字符串或 datetime 对象
        half_life_days: 半衰期（天）

    Returns:
        衰减后的权重因子 [0, 1]
    """
    # 确定参考时间点（优先使用最后强化时间，否则用创建时间）
    ref_time = None
    if last_reinforced_at:
        if isinstance(last_reinforced_at, datetime):
            ref_time = last_reinforced_at
        else:
            try:
                ref_time = datetime.fromisoformat(last_reinforced_at)
            except (ValueError, TypeError):
                pass

    if ref_time is None and created_at:
        if isinstance(created_at, datetime):
            ref_time = created_at
        else:
            try:
                ref_time = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                pass

    if ref_time is None:
        # 没有任何时间信息，返回完整权重
        return 1.0

    # 计算距离现在的时间差
    days_since = (datetime.now() - ref_time).total_seconds() / 86400.0

    # 指数衰减
    decay = math.pow(0.5, days_since / half_life_days)

    # 确保在 [0, 1] 范围内
    return max(0.0, min(1.0, decay))


def compute_salience_score(
    similarity: float,
    reinforcement_count: int,
    recency_decay: float,
) -> float:
    """
    计算 salience 分数。

    公式: salience = similarity × reinforcement_count × recency_decay

    Args:
        similarity: 向量相似度分数 [0, 1]
        reinforcement_count: 强化次数（最小为 1）
        recency_decay: 时间衰减因子 [0, 1]

    Returns:
        综合 salience 分数
    """
    # 强化次数最小为 1（从未被强化的记忆）
    reinforcement = max(1, reinforcement_count)

    # 综合分数
    return similarity * reinforcement * recency_decay


# ============================================================================
# Reinforcement MixIn
# ============================================================================

class ReinforcementMixIn:
    """
    为 MemorySystem 增加强化追踪能力的 MixIn 类。

    功能:
    1. 内容去重: 相同内容的记忆不会重复创建
    2. 强化追踪: 重复内容自动增加强化计数
    3. Salience 排序: 检索时考虑强化因子和时效性

    使用方式:
        # 方式 1: 直接继承
        class MyMemorySystem(ReinforcementMixIn, MemorySystem):
            pass

        # 方式 2: 组合使用
        reinforcement = ReinforcementMixIn()
        reinforcement.attach(memory_system)
    """

    def __init__(self):
        """初始化强化追踪 MixIn"""
        self._reinforce_enabled: bool = True
        self._salience_half_life_days: float = 7.0

    def attach(self, memory_system: "MemorySystem") -> None:
        """
        将 MixIn 的方法附加到 MemorySystem 实例。

        Args:
            memory_system: MemorySystem 实例
        """
        # 复制方法到 memory_system 实例
        for method_name in dir(self):
            if method_name.startswith("_") or method_name == "attach":
                continue
            method = getattr(self, method_name, None)
            if callable(method):
                setattr(memory_system, method_name, method)

        # 设置配置
        memory_system._reinforce_enabled = self._reinforce_enabled
        memory_system._salience_half_life_days = self._salience_half_life_days

    # =========================================================================
    # 强化写入
    # =========================================================================

    def write_with_reinforce(
        self,
        query_sentence: str,
        content: str = "",
        reference_duration: int = None,
        time_word: str = None,
        memory_type: str = "memory",
    ) -> str:
        """
        写入记忆，支持去重和强化。

        如果相同内容已存在（通过 content_hash 判断），强化它而不是创建新记录。

        Args:
            query_sentence: 查询句 "<时间><主体><动作><宾语><目的><结果>"
            content: 记忆内容（可留空）
            reference_duration: 参考生命周期（秒）
            time_word: 时间词（从查询句自动提取，可覆盖）
            memory_type: 记忆类型（用于哈希计算，默认为 "memory"）

        Returns:
            memory_id: 写入的记忆 ID（可能是已存在记忆的 ID）
        """
        self._check_initialized()

        # 计算 content_hash
        content_hash = compute_content_hash(content or query_sentence, memory_type)

        # 查找是否存在相同 hash 的记忆
        existing_id = self._find_by_content_hash(content_hash)

        if existing_id:
            # 强化已存在的记忆
            return self._reinforce_memory(existing_id)

        # 创建新记忆
        memory_id = self._write_new_memory(
            query_sentence=query_sentence,
            content=content,
            reference_duration=reference_duration,
            time_word=time_word,
            content_hash=content_hash,
            reinforcement_count=1,
        )

        return memory_id

    def _find_by_content_hash(self, content_hash: str) -> Optional[str]:
        """
        根据 content_hash 查找记忆 ID。

        Args:
            content_hash: 内容哈希

        Returns:
            memory_id，如果不存在返回 None
        """
        # 遍历所有记忆查找匹配的 content_hash
        # 注意: 这是一个 O(n) 操作，对于大规模数据可以考虑建立索引
        all_memories = self._get_all_memories_for_reinforce()
        for memory_id, memory in all_memories.items():
            extra = self._get_memory_extra(memory)
            if extra and extra.get("content_hash") == content_hash:
                return memory_id
        return None

    def _get_all_memories_for_reinforce(self) -> Dict[str, "Memory"]:
        """
        获取所有记忆用于强化查找。

        子类可以重写此方法以优化查询性能。

        Returns:
            {memory_id: Memory} 字典
        """
        # 默认实现：返回空字典，子类可以重写
        return {}

    def _get_memory_extra(self, memory: "Memory") -> Dict[str, Any]:
        """
        获取记忆的 extra 字段。

        Args:
            memory: Memory 对象

        Returns:
            extra 字典
        """
        # 尝试从 memory 对象的 extra 属性获取
        if hasattr(memory, "extra") and memory.extra:
            return memory.extra
        # 尝试从 memory 的 to_dict 中获取
        if hasattr(memory, "to_dict"):
            d = memory.to_dict()
            if "extra" in d:
                return d["extra"]
        return {}

    def _reinforce_memory(self, memory_id: str) -> str:
        """
        强化已存在的记忆。

        Args:
            memory_id: 记忆 ID

        Returns:
            强化的记忆 ID
        """
        # 获取当前记忆
        memory = self.get_memory(memory_id)
        if not memory:
            raise ValueError(f"记忆不存在: {memory_id}")

        # 获取当前 extra
        extra = self._get_memory_extra(memory)

        # 更新强化计数
        current_count = extra.get("reinforcement_count", 1)
        now = datetime.now()

        # 构建新的 extra
        new_extra = {
            **extra,
            "reinforcement_count": current_count + 1,
            "last_reinforced_at": now.isoformat(),
        }

        # 更新记忆的 extra
        self._update_memory_extra(memory_id, new_extra)

        return memory_id

    def _update_memory_extra(self, memory_id: str, extra: Dict[str, Any]) -> None:
        """
        更新记忆的 extra 字段。

        使用 VecDBCRUD.update() 方法更新 extra。

        Args:
            memory_id: 记忆 ID
            extra: 新的 extra 字典
        """
        self._vec_db.update(memory_id=memory_id, extra=extra)

    def _write_new_memory(
        self,
        query_sentence: str,
        content: str,
        reference_duration: int,
        time_word: str,
        content_hash: str,
        reinforcement_count: int,
    ) -> str:
        """
        创建新记忆（内部方法）。

        Args:
            query_sentence: 查询句
            content: 记忆内容
            reference_duration: 参考生命周期
            time_word: 时间词
            content_hash: 内容哈希
            reinforcement_count: 初始强化次数

        Returns:
            新创建的 memory_id
        """
        # 构建 extra
        now = datetime.now()
        extra = {
            "content_hash": content_hash,
            "reinforcement_count": reinforcement_count,
            "last_reinforced_at": now.isoformat(),
        }

        # 构建查询句
        if "<" not in query_sentence:
            query_sentence = self._build_query_sentence(
                time_word=time_word,
                subject=query_sentence,
            )

        # 解析槽位
        parts = self._parse_query_sentence(query_sentence)
        time_word_extracted = parts[0] or time_word or ""

        # 生命周期决策
        if self._lifecycle_mgr is not None:
            decided_lifecycle = self._lifecycle_mgr.decide_new_lifecycle(
                query_slots={k: v for k, v in zip(
                    ["scene", "subject", "action", "object", "purpose", "result"],
                    parts
                ) if v},
                reference_duration=reference_duration if reference_duration is not None else 86400,
            )
        else:
            decided_lifecycle = reference_duration if reference_duration is not None else 86400

        # 写入 VecDBCRUD
        embedding_mode = "interest" if self.enable_interest_mode else "raw"
        memory_id = self._vec_db.write(
            query_sentence=query_sentence,
            content=content,
            lifecycle=decided_lifecycle,
            embedding_mode=embedding_mode,
        )

        # 立即更新 extra
        self._vec_db.update(memory_id=memory_id, extra=extra)

        # 写入时间索引
        if time_word_extracted:
            self._time_index.add(
                memory_id=memory_id,
                time_word=time_word_extracted,
                created_at=now,
            )

        # 写入槽位索引
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
    # Salience 排序
    # =========================================================================

    def _salience_score(self, item: Dict[str, Any]) -> float:
        """
        计算单条记忆的 salience 分数。

        公式: salience = similarity × reinforcement_count × recency_decay

        Args:
            item: 包含以下字段的字典:
                - score: 向量相似度分数 [0, 1]
                - reinforcement_count: 强化次数（可选，默认 1）
                - last_reinforced_at: 最后强化时间（可选）
                - created_at: 创建时间（可选）

        Returns:
            salience 分数
        """
        # 获取相似度分数
        similarity = item.get("score", 0.0)

        # 获取强化次数
        reinforcement = item.get("reinforcement_count", 1)

        # 计算时间衰减
        recency_decay = compute_recency_decay(
            last_reinforced_at=item.get("last_reinforced_at"),
            created_at=item.get("created_at"),
            half_life_days=self._salience_half_life_days,
        )

        return compute_salience_score(similarity, reinforcement, recency_decay)

    def search_with_salience(
        self,
        query_slots: Dict[str, str],
        top_k: int = 5,
        use_subspace: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        使用 salience 分数进行搜索和排序。

        综合考虑:
        - 向量相似度
        - 强化次数
        - 时间衰减

        Args:
            query_slots: 查询槽位
            top_k: 返回数量
            use_subspace: 是否使用子空间搜索

        Returns:
            排序后的结果列表
        """
        # 执行基础搜索
        if use_subspace:
            results = self.search_subspace(query_slots, top_k=top_k * 2)
        else:
            results = self.search_fullspace(query_slots, top_k=top_k * 2)

        if not results:
            return []

        # 获取所有记忆的 extra 信息
        memory_ids = [r.memory_id for r in results]
        memories = self.get_memories(memory_ids)

        # 计算 salience 分数
        scored_items = []
        for result in results:
            memory = memories.get(result.memory_id)
            if not memory:
                continue

            extra = self._get_memory_extra(memory)

            # 构建 item
            item = {
                "memory_id": result.memory_id,
                "score": max(0.0, 1.0 - result.distance / 2.0),  # L2 distance -> similarity
                "reinforcement_count": extra.get("reinforcement_count", 1),
                "last_reinforced_at": extra.get("last_reinforced_at"),
                "created_at": memory.created_at.isoformat() if hasattr(memory, "created_at") else None,
                "memory": memory,
                "distance": result.distance,
                "metadata": result.metadata,
            }

            # 计算 salience
            salience = self._salience_score(item)
            item["salience_score"] = salience

            scored_items.append(item)

        # 按 salience 降序排序
        scored_items.sort(key=lambda x: x["salience_score"], reverse=True)

        return scored_items[:top_k]

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _check_initialized(self) -> None:
        """检查是否已初始化"""
        if hasattr(self, "_initialized") and not self._initialized:
            raise RuntimeError("MemorySystem 未初始化，请先调用 initialize()")

    def get_reinforcement_info(self, memory_id: str) -> Dict[str, Any]:
        """
        获取记忆的强化信息。

        Args:
            memory_id: 记忆 ID

        Returns:
            包含 reinforcement_count, last_reinforced_at, content_hash 的字典
        """
        memory = self.get_memory(memory_id)
        if not memory:
            return {}

        extra = self._get_memory_extra(memory)
        return {
            "reinforcement_count": extra.get("reinforcement_count", 1),
            "last_reinforced_at": extra.get("last_reinforced_at"),
            "content_hash": extra.get("content_hash"),
        }

    def list_strong_memories(self, min_reinforcement: int = 2) -> List[str]:
        """
        列出被强化过记忆的 ID。

        Args:
            min_reinforcement: 最小强化次数

        Returns:
            符合条件的 memory_id 列表
        """
        # 这是一个简化实现，实际可能需要索引支持
        result = []
        all_memories = self._get_all_memories_for_reinforce()

        for memory_id, memory in all_memories.items():
            extra = self._get_memory_extra(memory)
            count = extra.get("reinforcement_count", 1)
            if count >= min_reinforcement:
                result.append(memory_id)

        return result


# ============================================================================
# VecDBCRUD 扩展支持
# ============================================================================

def extend_vec_db_crud():
    """
    为 VecDBCRUD 添加 extra 字段支持的扩展。

    调用此函数后:
    - memories 表会增加 extra 列
    - write 方法支持 extra 参数
    - _kv_read 方法会读取 extra
    """
    from ..basic_crud.vec_db_crud import VecDBCRUD

    # 检查是否已经扩展
    if hasattr(VecDBCRUD, "_extra_column_added"):
        return

    # 添加 extra 列到表
    original_init_kv_db = VecDBCRUD._init_kv_db

    def new_init_kv_db(self):
        original_init_kv_db(self)
        # 检查 extra 列是否存在
        self.cursor.execute("""
            SELECT COUNT(*) FROM pragma_table_info('memories') WHERE name='extra'
        """)
        if self.cursor.fetchone()[0] == 0:
            self.cursor.execute("ALTER TABLE memories ADD COLUMN extra TEXT")
            self.conn.commit()

    VecDBCRUD._init_kv_db = new_init_kv_db

    # 扩展 write 方法以接受 extra 参数
    original_write = VecDBCRUD.write

    def new_write(
        self,
        query_sentence: str,
        content: str,
        lifecycle: int,
        embedding_mode: str = "interest",
        extra: dict = None,
    ) -> str:
        """写入一条记忆，支持 extra 参数"""
        from ..basic_crud.vec_db_crud import Memory

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
            extra=extra or {},
        )

        # 解析槽位字符串值
        parts = self._parse_query_sentence(query_sentence)
        slot_values = {name: parts[i] for i, name in enumerate(self.SLOT_NAMES)}

        # 选择用于全量 Collection 的向量
        full_vec = interest_vec if embedding_mode != "raw" else raw_vec

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

        return memory_id

    VecDBCRUD.write = new_write

    # 标记已扩展
    VecDBCRUD._extra_column_added = True


# ============================================================================
# MemorySystem 扩展
# ============================================================================

def extend_memory_system():
    """
    为 MemorySystem 添加强化追踪支持。

    调用此函数后:
    - write_with_reinforce 方法可用
    - search_with_salience 方法可用
    - get_reinforcement_info 方法可用
    """
    from ..pyapi.core import MemorySystem

    # 检查是否已经扩展
    if hasattr(MemorySystem, "_reinforce_extended"):
        return

    # 确保 VecDBCRUD 已扩展
    extend_vec_db_crud()

    # 混入 ReinforcementMixIn
    original_init = MemorySystem.__init__

    def new_init(
        self,
        name: str,
        persist_directory: str,
        vocab_size: int = 10000,
        enable_interest_mode: bool = True,
        similarity_threshold: float = 0.85,
        enable_reinforce: bool = True,
    ):
        original_init(self, name, persist_directory, vocab_size, enable_interest_mode, similarity_threshold)
        self._reinforce_enabled = enable_reinforce
        self._salience_half_life_days = 7.0

    MemorySystem.__init__ = new_init

    # 添加强化相关方法
    MemorySystem.write_with_reinforce = ReinforcementMixIn.write_with_reinforce
    MemorySystem.search_with_salience = ReinforcementMixIn.search_with_salience
    MemorySystem.get_reinforcement_info = ReinforcementMixIn.get_reinforcement_info
    MemorySystem.list_strong_memories = ReinforcementMixIn.list_strong_memories
    MemorySystem._salience_score = ReinforcementMixIn._salience_score
    MemorySystem._find_by_content_hash = ReinforcementMixIn._find_by_content_hash
    MemorySystem._reinforce_memory = ReinforcementMixIn._reinforce_memory
    MemorySystem._update_memory_extra = ReinforcementMixIn._update_memory_extra
    MemorySystem._get_memory_extra = ReinforcementMixIn._get_memory_extra
    MemorySystem._get_all_memories_for_reinforce = ReinforcementMixIn._get_all_memories_for_reinforce
    MemorySystem._write_new_memory = ReinforcementMixIn._write_new_memory

    # 标记已扩展
    MemorySystem._reinforce_extended = True


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "compute_content_hash",
    "compute_recency_decay",
    "compute_salience_score",
    "ReinforcementMixIn",
    "extend_vec_db_crud",
    "extend_memory_system",
]
