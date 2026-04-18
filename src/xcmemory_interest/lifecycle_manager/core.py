"""
星尘记忆 - 生命周期管理器核心实现

负责：
1. 新记忆的生命周期决定 (decide_new_lifecycle)
2. 老记忆的生命周期更新 (update_existing_lifecycles)
"""

from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import torch
import torch.nn.functional as F

from ..basic_crud import VecDBCRUD
from ..auxiliary_query import SQLDatabase
from ..embedding_coder import QuerySlots
from ..vector_db.reranker import ProbabilitySampler

# ============================================================================
# 常量
# ============================================================================

LIFECYCLE_INFINITY = 999999  # 永不过期的生命周期标记

# 访问触发的生命周期增长参数
SHORT_TERM_CAP = 7 * 86400      # 短期记忆上限：7天
LONG_TERM_CAP = 30 * 86400      # 长期记忆上限：30天
TRANSITION_CAP = 365 * 86400    # 跃迁临界值：365天（超过则跃迁到 infinity）
MIN_SCALE = 0.1                # Sigmoid 导数保底值（最小 10% 增量）


# ============================================================================
# LifecycleManager
# ============================================================================

class LifecycleManager:
    """
    生命周期管理器

    职责：
    1. 写入新记忆时决定其生命周期 (decide_new_lifecycle)
    2. 写入新记忆时更新相关老记忆的生命周期 (update_existing_lifecycles)

    依赖：
    - vec_db: VecDBCRUD 实例（用于向量搜索、获取记忆的查询句）
    - sql_db: SQLDatabase 实例（用于读取/更新生命周期）
    """

    LIFECYCLE_INFINITY = LIFECYCLE_INFINITY

    def __init__(
        self,
        vec_db: VecDBCRUD,
        sql_db: SQLDatabase,
        top_k: int = 20,
        sample_size: int = 5,
        sigma: float = None,
    ):
        """
        初始化 LifecycleManager

        Args:
            vec_db: VecDBCRUD 实例（向量数据库操作对象）
            sql_db: SQLDatabase 实例（SQL数据库操作对象）
            top_k: 被动回忆时检索的候选数量
            sample_size: 被动回忆后采样的记忆数量
            sigma: 概率采样的正态分布标准差（None=自适应）
        """
        self._vec_db = vec_db
        self._sql_db = sql_db
        self._top_k = top_k
        self._sample_size = sample_size
        self._sampler = ProbabilitySampler(sigma=sigma)

    # =========================================================================
    # 核心 API
    # =========================================================================

    def decide_new_lifecycle(
        self,
        query_slots: Dict[str, str],
        reference_duration: int,
    ) -> int:
        """
        决定新记忆的生命周期

        Args:
            query_slots: 查询句槽位 {"subject": "我", "action": "学习", ...}
            reference_duration: 参考生命周期（用户指定或默认）

        Returns:
            计算得出的生命周期值（有效期秒数）
        """
        # Step 1: 被动回忆
        candidates = self._vec_db.search_fullspace(
            query_slots=query_slots,
            top_k=self._top_k,
            embedding_mode="interest",
            use_slot_rerank=False,
        )

        if not candidates:
            # 无被动回忆时，使用参考生命周期
            return reference_duration

        # Step 2: 概率采样
        distances = [c.distance for c in candidates]
        sampled_candidates = self._sampler.sample(
            candidates=[{
                "memory_id": c.memory_id,
                "lifecycle": self.get_memory_lifecycle(c.memory_id) or reference_duration,
            } for c in candidates],
            distances=distances,
            n_select=self._sample_size,
        )

        if not sampled_candidates:
            return reference_duration

        # Step 3: 获取采样记忆的生命周期和采样权重
        sampled_lifecycles = [s["lifecycle"] for s in sampled_candidates]
        sample_weights = [s["sample_weight"] for s in sampled_candidates]

        # Step 4: 计算 current_duration
        current_duration = self._compute_current_duration(sampled_lifecycles, sample_weights)

        # Step 5: 计算 interest_duration
        interest_duration = self._compute_interest_duration(query_slots)

        # Step 6: 概率融合（对应 DESIGN §2 的三种 Duration）
        # duration_inputs 顺序: [current_d, interest_d, reference_d]
        lifecycle = self._fuse_durations(
            duration_inputs={
                "current_d": current_duration,
                "interest_d": interest_duration,
                "reference_d": float(reference_duration),
            },
        )
        lifecycle = int(max(1, lifecycle))

        # Step 7: 更新被动回忆到的老记忆的生命周期
        # 注意：interest_duration 复用 Step 5 的结果，不重算
        # 注意：sampled_candidates 含 sample_weight，用于决定 w 的权重
        self._update_existing_lifecycles(
            candidates=candidates,
            sampled_candidates=sampled_candidates,
            interest_duration_new=interest_duration,
            new_lifecycle=lifecycle,
        )

        return lifecycle

    def _update_existing_lifecycles(
        self,
        candidates,
        sampled_candidates: List[Dict],
        interest_duration_new: float,
        new_lifecycle: int,
    ) -> List[Tuple[str, int, int]]:
        """
        更新被动回忆中相关老记忆的生命周期（私有方法）

        由 decide_new_lifecycle 调用，复用其已搜索到的 candidates 和已计算的 interest_duration_new。

        计算公式：
            ratio = old_lc / new_lc
            f = sqrt(ratio)  --  老记忆比新记忆弱时削弱，强时增强
            w = sampled_prob  --  采样权重（离群点权重小，典型记忆权重大）

            new_lc = old_lc * (1 - w) + new_lc * f * w

        Args:
            candidates: 被动回忆结果（已在 decide_new_lifecycle 中搜索）
            sampled_candidates: 采样结果（含 sample_weight，来自 ProbabilitySampler.sample）
            interest_duration_new: 新记忆的查询句兴趣强度（已在 decide_new_lifecycle 中计算）
            new_lifecycle: 新记忆的生命周期

        Returns:
            [(memory_id, old_lifecycle, new_lifecycle), ...] 更新详情
        """
        # 构建 memory_id -> sample_weight 的映射
        sample_weight_map = {
            s["memory_id"]: s["sample_weight"] for s in sampled_candidates
        }

        # Step 1: 筛选 lifecycle ≠ ∞ 的记忆
        finite_candidates = []
        for c in candidates:
            lc = self.get_memory_lifecycle(c.memory_id)
            if lc is not None and lc < self.LIFECYCLE_INFINITY:
                finite_candidates.append({
                    "memory_id": c.memory_id,
                    "lifecycle": lc,
                    "distance": c.distance,
                    "sample_prob": sample_weight_map.get(c.memory_id, 0.0),
                })

        # Step 2: 遍历每条老记忆，计算并更新其生命周期
        update_details = []
        for cand in finite_candidates:
            memory_id = cand["memory_id"]
            old_lc = cand["lifecycle"]
            sampled_prob = cand["sample_prob"]

            # 获取老记忆的查询句（从 KV 数据库）
            memory = self._vec_db._kv_read(memory_id)
            if memory is None:
                continue

            # Step 3: 计算 interest_duration_old（暂保留，不参与新公式计算）
            old_slots = self._vec_db._slots_from_sentence(memory.query_sentence)
            interest_duration_old = self._compute_interest_duration_from_slots(old_slots)

            # Step 4: 计算 current_duration_old（用于 f 函数中的 ratio 参考）
            # 收集所有老记忆的生命周期 + 新记忆的生命周期
            n = len(finite_candidates)
            a = self._sample_size / n if n > 0 else 1.0

            old_weights = [a * self._sampler._normal_pdf(c["distance"]) for c in finite_candidates]
            new_weight = a * self._sampler._normal_pdf(0.0)  # 新数据 distance=0

            all_lifecycles = [c["lifecycle"] for c in finite_candidates] + [new_lifecycle]
            all_probs = old_weights + [new_weight]

            current_duration_old = self._compute_current_duration(all_lifecycles, all_probs)

            # Step 5: 计算老记忆的目标 lifecycle（作为 f 函数的输入参考）
            ref_lc = self._fuse_durations(
                duration_inputs={
                    "interest_d_old": interest_duration_old,
                    "current_d_old": current_duration_old,
                    "interest_d_new": interest_duration_new,
                },
            )

            # Step 6: 计算 new_lc（基于用户指定的新公式）
            #   ratio = old_lc / ref_lc
            #   f = sqrt(ratio)
            #   w = sampled_prob
            #   new_lc = old_lc * (1 - w) + ref_lc * f * w
            if ref_lc > 0:
                ratio = old_lc / ref_lc
                f = ratio ** 0.5  # sqrt(ratio)
            else:
                ratio = 1.0
                f = 1.0

            w = sampled_prob

            new_lc = int(max(1, old_lc * (1 - w) + ref_lc * f * w))

            # Step 7: 更新老记忆的生命周期
            if new_lc != old_lc:
                self.set_memory_lifecycle(memory_id, new_lc)
                update_details.append((memory_id, old_lc, new_lc))

        return update_details

    def on_memory_accessed(
        self,
        memory_id: str,
    ) -> List[Tuple[str, int, int]]:
        """
        当记忆被访问时，自动更新相关记忆的生命周期

        以被访问记忆为中心进行被动回忆，套用 _update_existing_lifecycles
        同款公式计算目标值，但增量通过 Sigmoid 导数函数衰减（比写入触发更微弱）。

        增长阶段：
        - 0 < old_lc < 短期临界值：使用短期 Sigmoid 导数
        - 短期临界值 <= old_lc < 跃迁临界值：使用长期 Sigmoid 导数
        - old_lc >= 跃迁临界值：跃迁到永不过期

        Args:
            memory_id: 被访问的记忆 ID

        Returns:
            [(memory_id, old_lifecycle, new_lifecycle), ...] 更新详情
        """
        # Step 1: 获取被访问记忆的信息
        accessed_memory = self._vec_db._kv_read(memory_id)
        if accessed_memory is None:
            return []

        accessed_lc = accessed_memory.lifecycle
        if accessed_lc >= LIFECYCLE_INFINITY:
            # 被访问记忆已经是永不过期，无需更新
            return []

        # Step 2: 以被访问记忆的查询句为中心进行被动回忆
        # 直接解析 query_sentence 得到字符串值
        from ..basic_crud.vec_db_crud import VecDBCRUD
        parts = VecDBCRUD._parse_query_sentence(accessed_memory.query_sentence)
        slot_names = ["time", "subject", "action", "object", "purpose", "result"]
        query_slots_dict = {
            slot_names[i]: parts[i]
            for i in range(len(parts))
            if parts[i]  # 过滤掉空字符串
        }
        # 用于后续计算 interest_duration（需要 QuerySlots 对象）
        accessed_slots = self._vec_db._slots_from_sentence(accessed_memory.query_sentence)

        if not query_slots_dict:
            return []

        candidates = self._vec_db.search_fullspace(
            query_slots=query_slots_dict,
            top_k=self._top_k,
            embedding_mode="interest",
            use_slot_rerank=False,
        )

        if not candidates:
            return []

        # Step 3: 概率采样
        distances = [c.distance for c in candidates]
        sampled_candidates = self._sampler.sample(
            candidates=[{
                "memory_id": c.memory_id,
                "lifecycle": self.get_memory_lifecycle(c.memory_id) or accessed_lc,
            } for c in candidates],
            distances=distances,
            n_select=self._sample_size,
        )

        if not sampled_candidates:
            return []

        # Step 4: 构建 memory_id -> sample_weight 的映射
        sample_weight_map = {
            s["memory_id"]: s["sample_weight"] for s in sampled_candidates
        }

        # Step 5: 筛选 lifecycle ≠ ∞ 的记忆（排除被访问的记忆自身）
        finite_candidates = []
        for c in candidates:
            if c.memory_id == memory_id:
                continue
            lc = self.get_memory_lifecycle(c.memory_id)
            if lc is not None and lc < self.LIFECYCLE_INFINITY:
                finite_candidates.append({
                    "memory_id": c.memory_id,
                    "lifecycle": lc,
                    "distance": c.distance,
                    "sample_prob": sample_weight_map.get(c.memory_id, 0.0),
                })

        # Step 6: 遍历每条老记忆，计算并更新其生命周期
        update_details = []
        for cand in finite_candidates:
            old_memory_id = cand["memory_id"]
            old_lc = cand["lifecycle"]
            sampled_prob = cand["sample_prob"]

            # 获取老记忆的查询句
            old_memory = self._vec_db._kv_read(old_memory_id)
            if old_memory is None:
                continue

            # 计算 interest_duration_old
            old_slots = self._vec_db._slots_from_sentence(old_memory.query_sentence)
            interest_duration_old = self._compute_interest_duration_from_slots(old_slots)

            # 计算 interest_duration_accessed（被访问记忆的兴趣强度）
            interest_duration_accessed = self._compute_interest_duration_from_slots(accessed_slots)

            # 计算 current_duration_old
            n = len(finite_candidates)
            a = self._sample_size / n if n > 0 else 1.0

            old_weights = [a * self._sampler._normal_pdf(c["distance"]) for c in finite_candidates]
            accessed_weight = a * self._sampler._normal_pdf(0.0)  # 被访问记忆 distance=0

            all_lifecycles = [c["lifecycle"] for c in finite_candidates] + [accessed_lc]
            all_probs = old_weights + [accessed_weight]

            current_duration_old = self._compute_current_duration(all_lifecycles, all_probs)

            # Step 7: 计算 interim_new_lc（_update_existing_lifecycles 同款公式）
            ref_lc = self._fuse_durations(
                duration_inputs={
                    "interest_d_old": interest_duration_old,
                    "current_d_old": current_duration_old,
                    "interest_d_new": interest_duration_accessed,
                },
            )

            if ref_lc > 0:
                ratio = old_lc / ref_lc
                f = ratio ** 0.5
            else:
                ratio = 1.0
                f = 1.0

            interim_new_lc = old_lc * (1 - sampled_prob) + ref_lc * f * sampled_prob

            # Step 8: 计算 actual_delta（通过 Sigmoid 导数衰减）
            new_lc = self._apply_access_decay(
                old_lc=old_lc,
                interim_new_lc=interim_new_lc,
            )

            # Step 9: 更新老记忆的生命周期
            if new_lc != old_lc:
                self.set_memory_lifecycle(old_memory_id, new_lc)
                update_details.append((old_memory_id, old_lc, new_lc))

        return update_details

    # =========================================================================
    # 辅助 API
    # =========================================================================

    def get_memory_lifecycle(self, memory_id: str) -> Optional[int]:
        """获取记忆的生命周期"""
        memory = self._vec_db._kv_read(memory_id)
        if memory is None:
            return None
        return memory.lifecycle

    def set_memory_lifecycle(self, memory_id: str, lifecycle: int) -> bool:
        """设置记忆的生命周期"""
        return self._vec_db.update(memory_id=memory_id, lifecycle=lifecycle)

    def is_infinite_lifecycle(self, lifecycle: int) -> bool:
        """判断是否为永不过期的生命周期"""
        return lifecycle >= self.LIFECYCLE_INFINITY

    def get_probability_sampler(self) -> ProbabilitySampler:
        """获取概率采样器"""
        return self._sampler

    # =========================================================================
    # 生命周期过期管理
    # =========================================================================

    def is_expired(self, memory_id: str, current_time: int = None) -> bool:
        """
        判断记忆是否已过期。

        过期条件: created_at + lifecycle < current_time（lifecycle 单位为秒）
        永不过期: lifecycle >= LIFECYCLE_INFINITY

        Args:
            memory_id: 记忆 ID
            current_time: 当前时间戳（Unix epoch），None=使用当前时间

        Returns:
            是否已过期
        """
        memory = self._vec_db._kv_read(memory_id)
        if memory is None:
            return True  # 不存在的记忆视为过期

        if self.is_infinite_lifecycle(memory.lifecycle):
            return False  # 永不过期

        if current_time is None:
            import time
            current_time = int(time.time())

        # lifecycle 字段语义：存储的是"有效期秒数"
        # 过期时间 = 创建时间 + lifecycle（秒）
        import time as _time
        created_ts = int(_time.mktime(memory.created_at.timetuple()))
        expires_ts = created_ts + memory.lifecycle

        return current_time >= expires_ts

    def filter_expired(
        self,
        memory_ids: List[str],
        current_time: int = None,
    ) -> List[str]:
        """
        过滤出已过期的记忆 ID 列表

        Args:
            memory_ids: 记忆 ID 列表
            current_time: 当前时间戳（Unix epoch），None=使用当前时间

        Returns:
            已过期的记忆 ID 列表
        """
        if current_time is None:
            import time
            current_time = int(time.time())

        expired = []
        for mid in memory_ids:
            if self.is_expired(mid, current_time):
                expired.append(mid)
        return expired

    def filter_alive(
        self,
        memory_ids: List[str],
        current_time: int = None,
    ) -> List[str]:
        """
        过滤出未过期的记忆 ID 列表

        Args:
            memory_ids: 记忆 ID 列表
            current_time: 当前时间戳（Unix epoch），None=使用当前时间

        Returns:
            未过期的记忆 ID 列表
        """
        if current_time is None:
            import time
            current_time = int(time.time())

        alive = []
        for mid in memory_ids:
            if not self.is_expired(mid, current_time):
                alive.append(mid)
        return alive

    def delete_expired(
        self,
        memory_ids: List[str] = None,
        dry_run: bool = False,
    ) -> List[str]:
        """
        删除过期的记忆

        Args:
            memory_ids: 要检查的记忆 ID 列表，None=检查所有记忆
            dry_run: True=只返回待删除列表，不实际删除

        Returns:
            已删除（或待删除）的记忆 ID 列表
        """
        if memory_ids is None:
            # 检查所有记忆
            memory_ids = self._get_all_memory_ids()

        expired_ids = self.filter_expired(memory_ids)

        if dry_run:
            return expired_ids

        # 实际删除
        deleted = []
        for mid in expired_ids:
            try:
                self._vec_db.delete(mid)
                self.delete_lifecycle_record(mid)
                deleted.append(mid)
            except Exception:
                pass

        return deleted

    def check_and_cleanup_all(self, batch_size: int = 100) -> Dict[str, Any]:
        """
        检查所有记忆的生命周期，延时删除过期数据

        这是一个批处理方法，用于定期清理。

        Args:
            batch_size: 每批处理的数量

        Returns:
            {
                "total": 总数,
                "expired": 已过期数量,
                "deleted": 已删除数量,
                "remaining": 剩余数量,
            }
        """
        all_ids = self._get_all_memory_ids()
        total = len(all_ids)

        deleted_ids = []
        for i in range(0, total, batch_size):
            batch = all_ids[i:i + batch_size]
            batch_deleted = self.delete_expired(batch)
            deleted_ids.extend(batch_deleted)

        return {
            "total": total,
            "expired": len(self.filter_expired(all_ids)),
            "deleted": len(deleted_ids),
            "remaining": total - len(deleted_ids),
        }

    def get_readable_and_cleanup(
        self,
        memory_ids: List[str],
        current_time: int = None,
    ) -> List[str]:
        """
        读取记忆时检查并删除过期数据（延时删除）

        在返回给用户之前，先检查哪些已过期并删除，只返回没过期的。

        Args:
            memory_ids: 要读取的记忆 ID 列表
            current_time: 当前时间戳（Unix epoch），None=使用当前时间

        Returns:
            未过期的记忆 ID 列表（已过期的已被删除）
        """
        if current_time is None:
            import time
            current_time = int(time.time())

        # 分离过期和未过期
        expired = []
        alive = []
        for mid in memory_ids:
            if self.is_expired(mid, current_time):
                expired.append(mid)
            else:
                alive.append(mid)

        # 删除过期的
        for mid in expired:
            try:
                self._vec_db.delete(mid)
                self.delete_lifecycle_record(mid)
            except Exception:
                pass

        return alive

    def delete_lifecycle_record(self, memory_id: str) -> bool:
        """
        删除记忆的生命周期记录（仅辅助表，不影响主 memories 表中的 lifecycle 字段）。

        注意：lifecycle 数据实际存储在 VecDBCRUD 的 memories 表中。
        此方法仅用于清理可能存在的 lifecycles 辅助表记录。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功删除（或表/记录不存在）
        """
        if self._sql_db is None:
            return True  # 没有辅助数据库，无需清理

        try:
            self._sql_db.delete(
                table_name="lifecycles",
                where={"memory_id": memory_id},
            )
            return True
        except Exception:
            # 表不存在或记录不存在，视为成功
            return True

    def _get_all_memory_ids(self) -> List[str]:
        """获取所有记忆 ID（从 VecDBCRUD 的 memories 表）"""
        try:
            cur = self._vec_db._conn.cursor()
            cur.execute("SELECT id FROM memories")
            return [row["id"] for row in cur.fetchall()]
        except Exception:
            return []

    def get_all_lifecycles(self) -> List[Dict[str, Any]]:
        """
        获取所有记忆的生命周期信息。

        数据来源：VecDBCRUD 的 memories 表（id, lifecycle, created_at）。

        Returns:
            [{"memory_id": id, "lifecycle": lc, "created_at": ts}, ...]
        """
        try:
            cur = self._vec_db._conn.cursor()
            cur.execute("SELECT id, lifecycle, created_at FROM memories")
            return [
                {
                    "memory_id": row["id"],
                    "lifecycle": row["lifecycle"],
                    "created_at": row["created_at"],
                }
                for row in cur.fetchall()
            ]
        except Exception:
            return []

    # =========================================================================
    # 内部计算方法
    # =========================================================================

    def _compute_current_duration(
        self,
        lifecycles: List[int],
        sample_weights: List[float],
    ) -> float:
        """
        计算 current_duration（解析版）

        基于采样权重对生命周期做加权平均。权重直接来自 ProbabilitySampler.sample()
        返回的 sample_weight（未归一化的正态分布概率密度 * 归一化因子 a）。

        语义：被动回忆越深刻（距离近、采样概率高）的记忆，对当前 lifecycle
        决定的影响越大。

        Args:
            lifecycles: 采样记忆的生命周期列表
            sample_weights: 采样权重列表（未归一化，来自 ProbabilitySampler.sample）

        Returns:
            current_duration 标量值
        """
        if not lifecycles:
            return 0.0

        # 加权平均生命周期（权重归一化）
        weights = np.array(sample_weights)
        total_weight = weights.sum()
        if total_weight > 0:
            normalized_weights = weights / total_weight
            weighted_lc = np.sum([lc * w for lc, w in zip(lifecycles, normalized_weights)])
        else:
            weighted_lc = np.mean(lifecycles)

        return float(weighted_lc)

    def _compute_interest_duration(self, query_slots: Dict[str, str]) -> float:
        """
        计算 interest_duration（解析版）

        使用经过自注意力处理后的槽位向量，计算各槽位的重要性分数。
        重要性分数 = 各槽位向量的 L2 范数之和。

        语义：查询句的信息量越大（各槽位向量越丰富），兴趣强度越高。

        Args:
            query_slots: 查询句槽位

        Returns:
            interest_duration 标量值
        """
        # 构建 QuerySlots
        slots = self._vec_db._slots_from_dict(query_slots)
        slot_dict = slots.to_dict()

        # 获取经自注意力处理后的槽位向量
        attended_vecs = self._get_attended_slot_vectors(slot_dict)

        # 重要性分数：各槽位向量 L2 范数之和
        total_score = 0.0
        for slot in ["time", "subject", "action", "object", "purpose", "result"]:
            vec = attended_vecs.get(slot)
            if vec is not None:
                total_score += float(np.linalg.norm(vec))

        return total_score

    def _compute_interest_duration_from_slots(self, slots) -> float:
        """
        从 QuerySlots 对象计算 interest_duration（解析版）

        Args:
            slots: QuerySlots 对象

        Returns:
            interest_duration 标量值
        """
        # 获取经自注意力处理后的槽位向量
        attended_vecs = self._get_attended_slot_vectors_from_slots(slots)

        # 重要性分数：各槽位向量 L2 范数之和
        total_score = 0.0
        for slot in ["time", "subject", "action", "object", "purpose", "result"]:
            vec = attended_vecs.get(slot)
            if vec is not None:
                total_score += float(np.linalg.norm(vec))

        return total_score

    def _get_attended_slot_vectors(
        self,
        slot_dict: Dict[str, Optional[torch.Tensor]],
    ) -> Dict[str, np.ndarray]:
        """
        获取经自注意力处理后的各槽位向量（用于计算 interest_duration）。

        Args:
            slot_dict: 槽位字典

        Returns:
            {slot_name: [64]} 经注意力处理后的槽位向量
        """
        # 构建 QuerySlots
        slots = QuerySlots(
            time=slot_dict.get("time"),
            subject=slot_dict.get("subject"),
            action=slot_dict.get("action"),
            object=slot_dict.get("object"),
            purpose=slot_dict.get("purpose"),
            result=slot_dict.get("result"),
        )
        slot_values = {
            "time": slots.time,
            "subject": slots.subject,
            "action": slots.action,
            "object": slots.object,
            "purpose": slots.purpose,
        }

        with torch.no_grad():
            _, slot_dict_out = self._vec_db.encoder.encode_query_with_ids_slots(
                time=slot_values.get("time"),
                subject=slot_values.get("subject"),
                action=slot_values.get("action"),
                object=slot_values.get("object"),
                purpose=slot_values.get("purpose"),
            )

            result = {}
            for slot, vec in slot_dict_out.items():
                result[slot] = vec.cpu().numpy()
            return result

    def _get_attended_slot_vectors_from_slots(
        self,
        slots: "QuerySlots",
    ) -> Dict[str, np.ndarray]:
        """
        从 QuerySlots 对象获取经自注意力处理后的各槽位向量。

        Args:
            slots: QuerySlots 对象

        Returns:
            {slot_name: [64]} 经注意力处理后的槽位向量
        """
        slot_dict = slots.to_dict()
        slot_values = {
            "time": slot_dict.get("time"),
            "subject": slot_dict.get("subject"),
            "action": slot_dict.get("action"),
            "object": slot_dict.get("object"),
            "purpose": slot_dict.get("purpose"),
        }

        with torch.no_grad():
            _, slot_dict_out = self._vec_db.encoder.encode_query_with_ids_slots(
                time=slot_values.get("time"),
                subject=slot_values.get("subject"),
                action=slot_values.get("action"),
                object=slot_values.get("object"),
                purpose=slot_values.get("purpose"),
            )

            result = {}
            for slot, vec in slot_dict_out.items():
                result[slot] = vec.cpu().numpy()
            return result

    def _fuse_durations(
        self,
        duration_inputs: Dict[str, float],
    ) -> float:
        """
        概率融合三种 Duration（解析版）

        步骤：
        1. 对三种 Duration 做 log-softmax 归一化（消除量纲差异）
        2. 再做一次 softmax 得到概率分布（和=1）
        3. 加权求和得到最终生命周期

        支持两组语义（通过 duration_inputs 中的 key 区分）：
        - decide_new_lifecycle:  {"current_d", "interest_d", "reference_d"}
        - update_existing_lifecycles: {"interest_d_old", "current_d_old", "interest_d_new"}

        Args:
            duration_inputs: 三种 Duration 的字典，必须恰好包含 3 个值

        Returns:
            融合后的生命周期值
        """
        if len(duration_inputs) != 3:
            raise ValueError(f"duration_inputs 必须包含 3 个值，当前: {list(duration_inputs.keys())}")

        # 按固定顺序取出值
        durations_list = list(duration_inputs.values())

        # Log-softmax 归一化 + softmax 得到概率（和=1）
        d_tensor = torch.tensor(durations_list, dtype=torch.float32)
        d_log_softmax = F.log_softmax(d_tensor, dim=0)
        probs = torch.softmax(d_log_softmax * 2.0, dim=0)  # 温度 T=2.0，可调

        # 加权求和
        lifecycle = (probs * d_tensor).sum().item()

        return float(lifecycle)

    def _apply_access_decay(
        self,
        old_lc: float,
        interim_new_lc: float,
        short_cap: float = SHORT_TERM_CAP,
        long_cap: float = LONG_TERM_CAP,
        transition_cap: float = TRANSITION_CAP,
        min_scale: float = MIN_SCALE,
    ) -> int:
        """
        应用访问触发的生命周期增长（Sigmoid 导数衰减）

        增量通过 Sigmoid 导数函数压缩，使增长越来越缓慢。

        阶段划分：
        - 0 < old_lc < short_cap：短期记忆阶段，Sigmoid 峰值 = short_cap/2
        - short_cap <= old_lc < transition_cap：长期记忆阶段，Sigmoid 峰值 = (short_cap+transition_cap)/2
        - old_lc >= transition_cap：跃迁到永不过期

        Args:
            old_lc: 老记忆当前生命周期
            interim_new_lc: _update_existing_lifecycles 同款公式计算的目标值
            short_cap: 短期记忆上限
            long_cap: 长期记忆上限
            transition_cap: 跃迁临界值
            min_scale: Sigmoid 导数保底值

        Returns:
            更新后的生命周期（整数秒）
        """
        delta = interim_new_lc - old_lc

        if delta <= 0:
            # 不需要增长，保持原值
            return int(old_lc)

        # 超过跃迁临界值，直接跃迁到永不过期
        if old_lc >= transition_cap:
            return LIFECYCLE_INFINITY

        # 确定所处阶段
        if old_lc < short_cap:
            # 短期记忆阶段
            mid = short_cap / 2.0
            k = 2.0 / short_cap
        else:
            # 长期记忆阶段
            mid = (short_cap + long_cap) / 2.0
            k = 2.0 / (long_cap - short_cap)

        # 计算带保底的 Sigmoid 导数
        scale = self._compute_sigmoid_derivative_with_floor(
            t=old_lc,
            mid=mid,
            k=k,
            min_scale=min_scale,
        )

        # 计算实际增量
        actual_delta = delta * scale

        return int(max(old_lc + 1, old_lc + actual_delta))

    def _compute_sigmoid_derivative_with_floor(
        self,
        t: float,
        mid: float,
        k: float,
        min_scale: float = MIN_SCALE,
    ) -> float:
        """
        带保底的 Sigmoid 导数

        原 Sigmoid 导数：k * sigmoid(k*(t-mid)) * (1 - sigmoid(k*(t-mid)))
        - 在 t = mid 处达到最大值 k/4
        - 在两端趋近于 0

        带保底版本：
        - 归一化：deriv / (k/4)，范围 [0, 1]
        - 乘以 (1 - min_scale)，范围 [0, 1-min_scale]
        - 加上 min_scale，范围 [min_scale, 1]

        效果：
        - 在 mid 处：scale ≈ 1（最大增长）
        - 在两端：scale ≈ min_scale（保底）
        - min_scale = 0.1 表示两端至少保留 10% 的增量

        Args:
            t: 当前生命周期
            mid: Sigmoid 中点
            k: Sigmoid 陡度参数
            min_scale: 最小 scale 保底值

        Returns:
            [min_scale, 1] 范围内的 scale 值
        """
        sig = 1.0 / (1.0 + np.exp(-k * (t - mid)))
        deriv = k * sig * (1.0 - sig)
        max_deriv = k / 4.0

        # 归一化到 [0, 1]，然后映射到 [min_scale, 1]
        normalized = deriv / max_deriv if max_deriv > 0 else 0.0
        scale = min_scale + (1.0 - min_scale) * normalized
        return float(scale)
