"""
星尘记忆 - 子空间搜索模块
Subspace Search: Slot-based Filtering + Full-space Reranking

设计思路：
1. 子空间过滤：用元数据中的槽位关键字/ID 做预过滤，缩小候选集
2. 全空间排序：用槽位分量向量在过滤后的候选集内做精确重排序
"""

import numpy as np
from typing import Optional, List, Dict, Any, Tuple, Callable
from .chroma_vector_db import ChromaVectorDB


# 槽位定义（与 InterestEncoder 一致）
SLOT_NAMES = ["time", "subject", "action", "object", "purpose", "result"]
SLOT_DIM = 64  # 每个槽位64维


class SubspaceSearcher:
    """
    子空间搜索器

    策略：
    1. 用槽位关键字或ID在 Chroma 中做元数据过滤（缩小候选集）
    2. 在候选集内，用槽位分量向量做精确的余弦相似度重排序

    这样做的好处：
    - 利用 Chroma 的索引加速初步筛选
    - 在小候选集上做精确的槽位分量匹配
    """

    def __init__(self, vector_db: ChromaVectorDB):
        """
        Args:
            vector_db: ChromaVectorDB 实例
        """
        self.db = vector_db

    def search(
        self,
        query_slots: Dict[str, Any],
        query_slot_vectors: Optional[Dict[str, np.ndarray]] = None,
        top_k: int = 5,
        subspace_filter: Optional[Dict[str, Any]] = None,
        rerank: bool = True,
        candidate_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        子空间搜索

        Args:
            query_slots: 查询槽位 dict，如 {"subject": "我", "action": "学"}
                           值可以是字符串关键字，也可以是槽位ID
            query_slot_vectors: 各槽位的查询向量 {"subject": [64], "action": [64]}
                                 如果提供，则在候选集内做分量重排序
            top_k: 返回的最终结果数
            subspace_filter: 额外的 Chroma 元数据过滤条件
            rerank: 是否在候选集内做重排序
            candidate_size: 子空间过滤后的候选集大小（用于重排序）

        Returns:
            List[Dict]: [{"id", "score", "metadata", "slot_distances"}, ...]
        """
        # Step 1: 构建 Chroma 元数据过滤条件
        where_filter = self._build_where_filter(query_slots, subspace_filter)

        # Step 2: 用完整向量做初步搜索（获取候选集）
        # 构造一个占位查询向量（实际上我们不使用完整向量相似度，
        # 只是利用 Chroma 的索引结构做初步筛选）
        if query_slot_vectors:
            # 如果有槽位向量，拼接成完整向量（用第一个已知槽位）
            query_vector = self._stack_slot_vectors(query_slot_vectors)
        else:
            # 没有槽位向量时，返回基于元数据过滤的结果
            return self._metadata_only_search(where_filter, top_k)

        # 初步搜索：候选集放大（用于后续重排序）
        initial_results = self.db.search(
            query_vector=query_vector,
            top_k=candidate_size,
            filter_metadata=where_filter if where_filter else None,
            include_embeddings=True,
        )

        if not initial_results:
            return []

        # Step 3: 在候选集内做槽位分量重排序
        if rerank and query_slot_vectors:
            reranked = self._rerank_by_slots(initial_results, query_slot_vectors)
        else:
            # 不重排序时，直接用 Chroma 返回的距离
            reranked = [
                {
                    "memory_id": r.get("memory_id") or r.get("id"),
                    "score": 1.0 - r.get("distance", 0) if r.get("distance") else 0.0,
                    "distance": r.get("distance", 0),
                    "metadata": r.get("metadata", {}),
                    "slot_distances": {},
                }
                for r in initial_results
            ]

        # 按分数排序返回 top_k
        reranked.sort(key=lambda x: x["score"], reverse=True)
        return reranked[:top_k]

    def _build_where_filter(
        self,
        query_slots: Dict[str, Any],
        extra_filter: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        构建 Chroma where 过滤条件

        策略：将已知的查询槽位转换为 $AND 条件
        Chroma where 语法: {"slot_name": {"$eq": "value"}}

        Args:
            query_slots: {"subject": "我", "purpose": "学习"}
            extra_filter: 额外过滤条件

        Returns:
            Chroma where dict 或 None
        """
        conditions = []

        for slot_name, slot_value in query_slots.items():
            if slot_value is None:
                continue
            if isinstance(slot_value, str):
                conditions.append({slot_name: {"$eq": slot_value}})
            elif isinstance(slot_value, (list, tuple)):
                # 多个可能值，用 $in
                conditions.append({slot_name: {"$in": list(slot_value)}})

        if not conditions:
            return extra_filter

        if len(conditions) == 1:
            where_filter = conditions[0]
        else:
            where_filter = {"$and": conditions}

        if extra_filter:
            # 合并额外条件
            if "$and" in where_filter:
                where_filter["$and"].append(extra_filter)
            else:
                where_filter = {"$and": [where_filter, extra_filter]}

        return where_filter

    def _metadata_only_search(
        self,
        where_filter: Optional[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """仅用元数据过滤的搜索（无向量相似度）"""
        # Chroma 不直接支持无向量搜索，我们用全零向量配合过滤
        # 这样会按向量相似度排序，但由于过滤条件，结果取决于 Chroma 内部顺序
        dummy_vector = np.zeros(SLOT_DIM * len(SLOT_NAMES))
        results = self.db.search(
            query_vector=dummy_vector,
            top_k=top_k,
            filter_metadata=where_filter,
            include_embeddings=True,
        )
        return [
            {
                "id": r["id"],
                "score": 1.0 - r["distance"] if r["distance"] else 0.0,
                "metadata": r["metadata"],
                "slot_distances": {},
            }
            for r in results
        ]

    def _stack_slot_vectors(self, slot_vectors: Dict[str, np.ndarray]) -> np.ndarray:
        """将槽位向量拼接为完整向量"""
        full = np.zeros(len(SLOT_NAMES) * SLOT_DIM)
        for i, slot in enumerate(SLOT_NAMES):
            if slot in slot_vectors:
                vec = np.array(slot_vectors[slot]).flatten()
                full[i * SLOT_DIM:(i + 1) * SLOT_DIM] = vec
        return full

    def _rerank_by_slots(
        self,
        candidates: List[Dict[str, Any]],
        query_slot_vectors: Dict[str, np.ndarray],
    ) -> List[Dict[str, Any]]:
        """
        在候选集内按槽位分量重排序

        排序策略：
        1. 先按"查询槽位在记忆中对应的槽位数量"升序（匹配少的更精准，排前面）
        2. 再按欧氏距离降序（距离近的排前面）

        Args:
            candidates: Chroma 返回的候选结果
            query_slot_vectors: 查询的槽位向量

        Returns:
            重排序后的结果
        """
        reranked = []

        for cand in candidates:
            metadata = cand.get("metadata", {}) or {}

            match_count = 0
            total_distance = 0.0
            slot_distances = {}

            for slot_name, query_vec in query_slot_vectors.items():
                # 从 metadata 中提取槽位向量
                slot_key = f"_slot_{slot_name}"
                stored_slot_vec = metadata.get(slot_key)

                if stored_slot_vec is not None:
                    stored_vec = np.array(stored_slot_vec).flatten()
                    query_vec = np.array(query_vec).flatten()

                    # 欧氏距离
                    dist = float(np.linalg.norm(query_vec - stored_vec))

                    match_count += 1
                    total_distance += dist
                    slot_distances[slot_name] = dist

            avg_distance = total_distance / match_count if match_count > 0 else float("inf")

            first_slot_name = next(iter(query_slot_vectors.keys()))
            reranked.append({
                "memory_id": cand.get("memory_id") or cand.get("id"),
                "match_count": match_count,
                "avg_distance": avg_distance,
                "distance": slot_distances.get(first_slot_name, 0.0),
                "metadata": metadata,
                "slot_distances": slot_distances,
            })

        # 排序：1. match_count 升序，2. avg_distance 升序
        reranked.sort(key=lambda x: (x["match_count"], x["avg_distance"]))

        return reranked

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """计算余弦相似度"""
        a = a.flatten()
        b = b.flatten()
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def search_with_context(
        self,
        query_slots: Dict[str, Any],
        query_slot_vectors: Optional[Dict[str, np.ndarray]] = None,
        context_memory_ids: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        带上下文扩展的搜索

        当知道一些相关记忆的 ID 时，可以利用图关系扩展搜索范围

        Args:
            query_slots: 查询槽位
            context_memory_ids: 上下文记忆 ID 列表
            top_k: 返回数量
        """
        # 基础子空间搜索
        results = self.search(
            query_slots=query_slots,
            query_slot_vectors=query_slot_vectors,
            top_k=top_k * 2,  # 多取一些，后续可能合并
            rerank=True,
        )

        # 如果有上下文记忆，可以做额外的图扩展
        # 这里先不做，留给 graph_query 模块处理
        return results[:top_k]


class HybridSearcher:
    """
    混合搜索器

    融合三种搜索模式：
    1. 关键字搜索（BM25/关键词匹配）
    2. 向量搜索（语义相似度）
    3. 图搜索（结构关系）

    策略：RRF (Reciprocal Rank Fusion) 融合
    """

    def __init__(self, vector_db: ChromaVectorDB, k: int = 60):
        """
        Args:
            vector_db: ChromaVectorDB 实例
            k: RRF 融合参数，越大越强调低名次结果
        """
        self.vector_db = vector_db
        self.subspace_searcher = SubspaceSearcher(vector_db)
        self.k = k  # RRF 参数

    def search(
        self,
        query_slots: Dict[str, Any],
        query_vector: np.ndarray,
        query_slot_vectors: Optional[Dict[str, np.ndarray]] = None,
        keyword_scores: Optional[Dict[str, float]] = None,
        graph_scores: Optional[Dict[str, float]] = None,
        top_k: int = 5,
        subspace_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        混合搜索

        Args:
            query_slots: 查询槽位
            query_vector: 完整查询向量 [384]
            query_slot_vectors: 槽位分量向量
            keyword_scores: 关键字搜索结果 {"mem_id": score}
            graph_scores: 图搜索结果 {"mem_id": score}
            top_k: 返回数量
            subspace_filter: 子空间过滤条件

        Returns:
            融合排序后的结果
        """
        # 1. 向量搜索
        vector_results = self.subspace_searcher.search(
            query_slots=query_slots,
            query_slot_vectors=query_slot_vectors,
            top_k=top_k * 3,
            subspace_filter=subspace_filter,
            rerank=True,
        )

        # 2. 收集所有候选记忆 ID
        all_ids = set(r["id"] for r in vector_results)

        if keyword_scores:
            all_ids.update(keyword_scores.keys())
        if graph_scores:
            all_ids.update(graph_scores.keys())

        # 3. 构建 RRF 分数表
        rrf_scores = {}
        for mem_id in all_ids:
            rrf_scores[mem_id] = 0.0

        # 向量搜索 RRF
        for rank, result in enumerate(vector_results):
            mem_id = result["id"]
            rrf_scores[mem_id] += 1.0 / (self.k + rank + 1)

        # 关键字搜索 RRF
        if keyword_scores:
            sorted_kw = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (mem_id, score) in enumerate(sorted_kw):
                rrf_scores[mem_id] += 1.0 / (self.k + rank + 1) * score

        # 图搜索 RRF
        if graph_scores:
            sorted_graph = sorted(graph_scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (mem_id, score) in enumerate(sorted_graph):
                rrf_scores[mem_id] += 1.0 / (self.k + rank + 1) * score

        # 4. 获取完整的记忆信息
        final_results = []
        for mem_id, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]:
            mem_data = self.vector_db.get(mem_id)
            if mem_data:
                mem_data["score"] = score
                final_results.append(mem_data)

        return final_results
