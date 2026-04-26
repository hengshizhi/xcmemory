"""
星尘记忆 - 全空间重排序模块
Full-space Reranking for Subspace-filtered Candidates

设计思路：
- 子空间过滤后，候选集较小（数十到数百）
- 在小候选集上做全空间槽位分量的精确匹配
- 支持多种重排序策略：余弦、EUCLIDEAN、加权槽位
"""

import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class RerankConfig:
    """重排序配置"""
    # 槽位权重（用于加权融合）
    slot_weights: Dict[str, float] = None
    # 是否使用欧氏距离（False=余弦）
    use_euclidean: bool = False
    # 是否考虑时间衰减
    time_decay: float = 0.0  # 0 = 不衰减，越大衰减越快
    # 结果槽位条件概率权重
    use_result_conditioning: bool = False

    def __post_init__(self):
        if self.slot_weights is None:
            # 默认均匀权重
            self.slot_weights = {
                "scene": 1.0,
                "subject": 1.0,
                "action": 1.0,
                "object": 1.0,
                "purpose": 1.0,
                "result": 1.0,
            }


class SubspaceReranker:
    """
    子空间结果重排序器

    排序策略：
    1. 先按"查询槽位在记忆中对应的槽位数量"升序（匹配少的更精准，排前面）
    2. 再按欧氏距离降序（距离近的排前面）
    """

    def __init__(self, config: Optional[RerankConfig] = None):
        self.config = config or RerankConfig()

    def rerank(
        self,
        candidates: List[Dict[str, Any]],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> List[Dict[str, Any]]:
        """
        对候选集重排序

        Args:
            candidates: 候选结果列表
            query_slot_vectors: 查询的槽位向量 {"slot": [64]}

        Returns:
            重排序后的列表
        """
        scored = []

        for cand in candidates:
            metadata = cand.get("metadata", {}) or {}
            match_count, avg_distance, slot_distances = self._compute_match_info(
                metadata, query_slot_vectors
            )

            scored.append({
                **cand,
                "match_count": match_count,
                "avg_distance": avg_distance,
                "slot_distances": slot_distances,
            })

        # 排序：
        # 1. match_count 升序（匹配少的排前面）
        # 2. avg_distance 降序（距离近的排前面，即距离大的 similarity 排前面）
        scored.sort(key=lambda x: (x["match_count"], -x["avg_distance"]))

        return scored

    def _compute_match_info(
        self,
        metadata: Dict[str, Any],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> tuple:
        """
        计算匹配信息

        Returns:
            (match_count, avg_distance, slot_distances)
            - match_count: 查询槽位在记忆中对应的数量
            - avg_distance: 平均欧氏距离
            - slot_distances: 各槽位的欧氏距离
        """
        match_count = 0
        total_distance = 0.0
        slot_distances = {}

        for slot_name, query_vec in query_slot_vectors.items():
            slot_key = f"_slot_{slot_name}"
            stored_vec = metadata.get(slot_key)

            if stored_vec is None:
                continue

            match_count += 1
            stored_vec = np.array(stored_vec).flatten()
            query_vec = np.array(query_vec).flatten()

            dist = float(np.linalg.norm(query_vec - stored_vec))
            slot_distances[slot_name] = dist
            total_distance += dist

        avg_distance = total_distance / match_count if match_count > 0 else float("inf")

        return match_count, avg_distance, slot_distances


class ResultConditioningReranker(SubspaceReranker):
    """
    带结果槽条件概率的重排序器

    核心思想：
    - 结果槽（result）由前5槽（scene/subject/action/object/purpose）条件决定
    - 查询中某些槽会"调制"结果槽的条件概率

    例如：
    - 查询: subject=我, purpose=学习
    - 候选: subject=我, purpose=工作, result=加薪
    - 条件概率: P(result=加薪 | subject=我, purpose=工作) 可能较高
    """

    def __init__(self, config: Optional[RerankConfig] = None):
        super().__init__(config)
        self.config.use_result_conditioning = True
        # 结果条件概率表（由在线学习模块提供）
        self._result_conditional_probs: Dict[str, Dict[str, float]] = {}

    def set_conditional_probs(self, probs: Dict[str, Dict[str, float]]):
        """设置结果条件概率表"""
        self._result_conditional_probs = probs

    def _compute_score(
        self,
        metadata: Dict[str, Any],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> tuple:
        """带结果条件概率的分数计算"""
        # 基础分数（前5槽的槽位匹配）
        base_score, slot_scores = super()._compute_score(metadata, query_slot_vectors)

        # 结果条件概率调整
        if self.config.use_result_conditioning and self._result_conditional_probs:
            result_adjustment = self._compute_result_adjustment(metadata, query_slot_vectors)
            base_score = base_score * (1.0 + result_adjustment)

        return base_score, slot_scores

    def _compute_result_adjustment(
        self,
        metadata: Dict[str, Any],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> float:
        """计算结果条件概率对分数的调整"""
        stored_result = metadata.get("result")
        if stored_result is None:
            return 0.0

        # 简化：查找 P(result | purpose) 条件概率
        stored_purpose = metadata.get("purpose")
        prob_key = f"purpose={stored_purpose}"

        if prob_key in self._result_conditional_probs:
            result_probs = self._result_conditional_probs[prob_key]
            cond_prob = result_probs.get(stored_result, 0.0)
            return cond_prob * 0.1

        return 0.0


class DynamicReranker:
    """
    动态重排序器

    根据查询上下文动态选择重排序策略：
    - 结果导向查询 → 加强 result 槽权重
    - 时间敏感查询 → 启用时间衰减
    - 实体聚焦查询 → 加强 subject/object 权重
    """

    def __init__(self):
        self.base_reranker = SubspaceReranker()
        self.result_reranker = ResultConditioningReranker()

    def rerank(
        self,
        candidates: List[Dict[str, Any]],
        query_slot_vectors: Dict[str, np.ndarray],
        query_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """根据查询上下文动态重排序"""
        context = query_context or {}

        if context.get("result_focused"):
            config = RerankConfig()
            config.slot_weights = {
                "scene": 0.5,
                "subject": 0.8,
                "action": 1.0,
                "object": 0.8,
                "purpose": 1.0,
                "result": 2.0,
            }
            reranker = ResultConditioningReranker(config)
        elif context.get("time_sensitive"):
            config = RerankConfig()
            config.time_decay = 0.01
            config.slot_weights = {
                "scene": 2.0,
                "subject": 1.0,
                "action": 1.0,
                "object": 1.0,
                "purpose": 1.0,
                "result": 1.0,
            }
            reranker = SubspaceReranker(config)
        elif context.get("entity_centric"):
            config = RerankConfig()
            config.slot_weights = {
                "scene": 0.5,
                "subject": 2.0,
                "action": 0.8,
                "object": 2.0,
                "purpose": 0.8,
                "result": 1.0,
            }
            reranker = SubspaceReranker(config)
        else:
            reranker = self.base_reranker

        return reranker.rerank(candidates, query_slot_vectors)


class BatchReranker:
    """批量重排序器"""

    def __init__(self, reranker: SubspaceReranker):
        self.reranker = reranker

    def rerank_batch(
        self,
        memory_candidates: List[List[Dict[str, Any]]],
        query_slot_vectors: List[Dict[str, np.ndarray]],
    ) -> List[List[Dict[str, Any]]]:
        """批量重排序"""
        results = []
        for candidates, query_vecs in zip(memory_candidates, query_slot_vectors):
            reranked = self.reranker.rerank(candidates, query_vecs)
            results.append(reranked)
        return results


class ProbabilitySampler:
    """
    基于距离正态分布的概率采样器

    设计：
    1. top_k(n): 用较大 n 检索候选集
    2. 定义 a = N(n) / n, a < 1 (归一化因子)
    3. 对于距离 L，定义 x=0 时概率最大的正态分布 f(X=L)
    4. 选中概率 P(L) = a * f(L)

    正态分布概率密度函数：
        f(L) = (1 / (σ * sqrt(2π))) * exp(-(L - μ)² / (2σ²))
    其中 μ = 0（x=0 时概率最大），σ 是标准差（控制分布宽度）
    """

    def __init__(self, sigma: float = None, random_seed: Optional[int] = None):
        """
        Args:
            sigma: 正态分布标准差，控制分布宽度
                  - σ 越小，分布越尖锐，距离近的候选被选中概率越高
                  - σ 越大，分布越平坦，各候选概率差异越小
                  - 默认 None: 使用平均距离尺度 sqrt(dim) 作为 sigma
            random_seed: 随机种子（用于可重现性）
        """
        self._sigma_param = sigma  # 保存原始参数
        self.sigma = sigma  # 会在 sample 时根据数据设置
        self._rng = np.random.default_rng(random_seed)

    def sample(
        self,
        candidates: List[Dict[str, Any]],
        query_vector: np.ndarray = None,
        top_k: int = None,
        n_select: int = None,
        distances: List[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        概率采样

        支持两种调用方式：
        1. 新版（reranker 用）：传入 query_vector，由内部计算距离
           sample(candidates, query_vector, top_k, n_select)
        2. 旧版（lifecycle_manager 用）：直接传入预计算好的 distances
           sample(candidates, distances=dist_list, n_select=n)

        Args:
            candidates: 候选列表
            query_vector: [384] 查询向量（新版模式）
            top_k: Chroma 返回的候选数量（新版模式）
            n_select: 最终选择的数量
            distances: 预计算的距离列表（旧版模式，优先级更高）

        Returns:
            概率采样选中的候选列表
        """
        if not candidates:
            return []

        n_candidates = len(candidates)

        # 旧版模式：直接使用预计算的距离
        if distances is not None:
            if n_select is None:
                n_select = min(len(distances), n_candidates)
            else:
                n_select = min(n_select, n_candidates)

            temp_dists = list(distances)

            # 自适应 sigma
            if self._sigma_param is None:
                if temp_dists:
                    self.sigma = max(np.mean(temp_dists) * 0.5, 0.1)
                else:
                    self.sigma = 1.0

            # 归一化因子 a = n_select / N
            a = n_select / n_candidates if n_candidates > 0 else 1.0

            weights = np.array([a * self._normal_pdf(d) for d in temp_dists])
            total = weights.sum()
            if total > 0:
                norm_probs = weights / total
            else:
                norm_probs = np.ones(n_candidates) / n_candidates

            selected_indices = self._rng.choice(
                n_candidates,
                size=n_select,
                replace=True,
                p=norm_probs,
            )

            seen = set()
            result = []
            for idx in selected_indices:
                if idx not in seen:
                    seen.add(idx)
                    cand = candidates[idx].copy()
                    cand["sample_weight"] = float(weights[idx])
                    cand["distance"] = float(temp_dists[idx])
                    result.append(cand)
            return result

        # 新版模式：需要从 query_vector 计算距离
        if query_vector is None or top_k is None or n_select is None:
            raise ValueError("新版模式需要 query_vector, top_k, n_select")

        n_select = min(n_select, n_candidates)

        # 计算每个候选的距离
        temp_dists = []
        for cand in candidates:
            vec = cand.get("vector") or cand.get("embedding")
            if vec is None:
                dist = cand.get("distance", 0.0)
            else:
                vec = np.array(vec).flatten()
                dist = float(np.linalg.norm(query_vector - vec))
            temp_dists.append(dist)

        # 自适应 sigma
        if self._sigma_param is None:
            if temp_dists:
                self.sigma = max(np.mean(temp_dists) * 0.5, 0.1)
            else:
                self.sigma = 1.0

        # 归一化因子 a = n_select / top_k
        a = n_select / top_k

        probs = np.array([a * self._normal_pdf(d) for d in temp_dists])

        total = probs.sum()
        if total > 0:
            norm_probs = probs / total * n_select
        else:
            norm_probs = np.ones(n_candidates) / n_candidates * n_select

        selected_indices = self._rng.choice(
            n_candidates,
            size=n_select,
            replace=True,
            p=norm_probs / norm_probs.sum(),
        )

        seen = set()
        result = []
        for idx in selected_indices:
            if idx not in seen:
                seen.add(idx)
                cand = candidates[idx]
                vec = cand.get("vector") or cand.get("embedding")
                if vec is not None:
                    vec = np.array(vec).flatten()
                    dist = float(np.linalg.norm(vec - query_vector))
                else:
                    dist = cand.get("distance", 0.0)
                result.append({
                    **cand,
                    "sample_prob": float(probs[idx]),
                    "distance": dist,
                })

        return result

    def _normal_pdf(self, x: float) -> float:
        """正态分布概率密度函数（μ=0）"""
        return (1.0 / (self.sigma * np.sqrt(2 * np.pi))) * np.exp(-(x ** 2) / (2 * self.sigma ** 2))


class DistanceAwareSampler:
    """
    距离感知的确定性采样器

    与 ProbabilitySampler 不同：
    - 确定性：基于距离的截断采样
    - 无随机性：每次相同输入得到相同输出
    - 适合需要可重现结果的场景
    """

    def __init__(self, distance_threshold: float = 1.5):
        """
        Args:
            distance_threshold: 距离阈值，超过则不入选
        """
        self.distance_threshold = distance_threshold

    def sample(
        self,
        candidates: List[Dict[str, Any]],
        query_vector: np.ndarray,
        n_select: int,
    ) -> List[Dict[str, Any]]:
        """
        确定性采样

        策略：
        1. 按距离升序排序
        2. 距离 < threshold 的候选直接入选
        3. 如果不足 n_select，从剩余候选中按距离补足
        """
        if not candidates or n_select <= 0:
            return []

        # 计算每个候选的距离
        scored = []
        for cand in candidates:
            vec = cand.get("vector")
            if vec is None:
                vec = cand.get("embedding")
            if vec is not None:
                vec = np.array(vec).flatten()
                dist = float(np.linalg.norm(query_vector - vec))
            else:
                dist = cand.get("distance", 0.0)
            scored.append({**cand, "distance": dist})

        # 按距离升序排序
        scored.sort(key=lambda x: x["distance"])

        result = []
        remaining = []

        for cand in scored:
            if cand["distance"] < self.distance_threshold:
                result.append(cand)
            else:
                remaining.append(cand)

        # 如果不足 n_select，从 remaining 补足
        if len(result) < n_select:
            needed = n_select - len(result)
            result.extend(remaining[:needed])

        return result[:n_select]
