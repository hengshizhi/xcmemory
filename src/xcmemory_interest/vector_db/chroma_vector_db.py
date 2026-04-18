"""
星尘记忆 - 向量数据库封装
Chroma-based Vector Database for StarDust Memory

职责：
- 向量存储和检索（通过 Chroma）
- 子空间检索（按槽位过滤）
- 混合检索（向量+关键字+图）

注意：记忆内容（content）不在此处存储，由 basic_crud 层负责。
向量数据库只返回 memory_id，调用方通过 memory_id 从 basic_crud 获取完整记忆。
"""

import uuid
from typing import Optional, List, Dict, Any, Tuple

import chromadb
from chromadb.config import Settings
import numpy as np

from .reranker import SubspaceReranker, ResultConditioningReranker, DynamicReranker


class ChromaVectorDB:
    """
    Chroma 向量数据库封装

    数据分离设计：
    - Chroma: 只存储 memory_id 和向量，用于快速检索
    - basic_crud: 存储完整的 Memory 对象（包括 content）

    Chroma Collection 元数据只存必要的槽位信息，用于子空间过滤。
    """

    def __init__(
        self,
        persist_directory: str = "./data/vector_db",
        collection_name: str = "memory_vectors",
        enable_subspace: bool = True,
    ):
        """
        Args:
            persist_directory: Chroma 持久化目录
            collection_name: Collection 名称
            enable_subspace: 是否启用子空间检索
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.enable_subspace = enable_subspace

        # 初始化 Chroma Client
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False),
        )

        # 获取或创建 Collection
        # 注意：Chroma 的 embedding_function 我们不使用，而是自己传入向量
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "StarDust Memory vector storage"},
        )

        # 槽位定义
        self.slot_names = ["time", "subject", "action", "object", "purpose", "result"]
        self.slot_dim = 64
        self.vector_dim = 384  # 6 * 64

        # 重排序器
        self.subspace_reranker = SubspaceReranker()
        self.result_reranker = ResultConditioningReranker()
        self.dynamic_reranker = DynamicReranker()

    def _generate_id(self) -> str:
        """生成唯一 ID"""
        return f"mem_{uuid.uuid4().hex[:12]}"

    def add(
        self,
        vector: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None,
    ) -> str:
        """
        添加向量到数据库

        Args:
            vector: [384] 记忆向量
            metadata: 元数据（可选，用于子空间过滤）
                     只存槽位名称，如 {"subject": "我", "action": "学"}
                     不存 content，content 在 basic_crud 层
            memory_id: 指定的 memory_id，不指定则自动生成

        Returns:
            memory_id: 添加的记忆 ID
        """
        if memory_id is None:
            memory_id = self._generate_id()

        # 标准化向量
        vector = np.array(vector, dtype=np.float32)
        if vector.shape != (self.vector_dim,):
            raise ValueError(f"向量维度必须为 {self.vector_dim}，实际为 {vector.shape}")

        # L2 归一化（用于余弦相似度）
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm

        # 存储
        self.collection.add(
            ids=[memory_id],
            embeddings=[vector.tolist()],
            metadatas=[metadata or {}],
        )

        return memory_id

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, str]] = None,
        include_vectors: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        全空间向量检索

        Args:
            query_vector: [384] 查询向量
            top_k: 返回数量
            filter_metadata: Chroma where 过滤条件
            include_vectors: 是否返回向量

        Returns:
            List[{"memory_id": str, "distance": float, "metadata": dict, "vector": np.ndarray}]
        """
        # 标准化查询向量
        query_vector = np.array(query_vector, dtype=np.float32)
        if query_vector.shape != (self.vector_dim,):
            raise ValueError(f"查询向量维度必须为 {self.vector_dim}，实际为 {query_vector.shape}")

        norm = np.linalg.norm(query_vector)
        if norm > 0:
            query_vector = query_vector / norm

        # Chroma 查询
        results = self.collection.query(
            query_embeddings=[query_vector.tolist()],
            n_results=top_k,
            where=filter_metadata,
            include=["metadatas", "distances", "embeddings"] if include_vectors else ["metadatas", "distances"],
        )

        # 解析结果
        output = []
        if results["ids"] and len(results["ids"]) > 0:
            for i, mem_id in enumerate(results["ids"][0]):
                item = {
                    "memory_id": mem_id,
                    "distance": results["distances"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                }
                if include_vectors and results.get("embeddings") and len(results.get("embeddings", [[]])[0]) > 0:
                    item["vector"] = np.array(results["embeddings"][0][i])
                output.append(item)

        return output

    def get(self, memory_id: str, include_vector: bool = False) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取向量

        Args:
            memory_id: 记忆 ID
            include_vector: 是否返回向量

        Returns:
            {"memory_id": str, "vector": np.ndarray, "metadata": dict} 或 None
        """
        results = self.collection.get(
            ids=[memory_id],
            include=["metadatas", "embeddings"] if include_vector else ["metadatas"],
        )

        if not results["ids"] or len(results["ids"]) == 0:
            return None

        output = {
            "memory_id": results["ids"][0],
            "metadata": results["metadatas"][0] if results["metadatas"] else {},
        }

        if include_vector:
            embeddings = results.get("embeddings")
            if embeddings is not None and len(embeddings) > 0 and len(embeddings[0]) > 0:
                output["vector"] = np.array(embeddings[0][0])

        return output

    def update(
        self,
        memory_id: str,
        vector: Optional[np.ndarray] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        更新向量或元数据

        Args:
            memory_id: 记忆 ID
            vector: 新向量（可选）
            metadata: 新元数据（可选）

        Returns:
            是否更新成功
        """
        update_dict = {}

        if vector is not None:
            vector = np.array(vector, dtype=np.float32)
            norm = np.linalg.norm(vector)
            if norm > 0:
                vector = vector / norm
            update_dict["embeddings"] = [vector.tolist()]

        if metadata is not None:
            update_dict["metadatas"] = [metadata]

        if not update_dict:
            return False

        try:
            self.collection.update(
                ids=[memory_id],
                **update_dict,
            )
            return True
        except Exception:
            return False

    def delete(self, memory_id: str) -> bool:
        """
        删除向量

        Args:
            memory_id: 记忆 ID

        Returns:
            是否删除成功
        """
        try:
            self.collection.delete(ids=[memory_id])
            return True
        except Exception:
            return False

    def close(self):
        """关闭数据库连接（释放文件锁）"""
        self.client = None
        self.collection = None

    def count(self) -> int:
        """返回向量总数"""
        return self.collection.count()

    def exists(self, memory_id: str) -> bool:
        """检查 memory_id 是否存在"""
        return self.get(memory_id) is not None

    def clear(self):
        """清空所有向量（危险操作）"""
        self.client.delete_collection(name=self.collection_name)
        self.collection = self.client.create_collection(
            name=self.collection_name,
            metadata={"description": "StarDust Memory vector storage"},
        )


class SubspaceSearcher:
    """
    子空间搜索器

    流程：
    1. 在 Chroma 中用 where 条件过滤（槽位精确匹配）
    2. 对候选集用槽位分量向量重排序

    注意：
    - 槽位过滤依赖 metadata，但 metadata 只存字符串值
    - 真正的向量级过滤在重排序阶段完成
    """

    def __init__(self, vector_db: ChromaVectorDB):
        self.db = vector_db
        self.reranker = vector_db.subspace_reranker

    def search(
        self,
        query_vector: np.ndarray,
        query_slot_vectors: Dict[str, np.ndarray],
        top_k: int = 5,
        rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        子空间搜索

        注意：Chroma 只存储完整向量 [384]，子空间过滤依赖 metadata 中的槽位值。

        Args:
            query_vector: [384] 完整查询向量（用于 Chroma 初步检索）
            query_slot_vectors: 各槽位的查询向量，如 {"subject": [64]}（用于重排序）
            top_k: 返回数量
            rerank: 是否对候选集重排序

        Returns:
            List[{"memory_id": str, "distance": float, "metadata": dict}]
        """
        # 用完整向量在 Chroma 中检索
        candidates = self.db.search(
            query_vector=query_vector,
            top_k=top_k * 10,  # 获取更多候选
        )

        if not candidates:
            return []

        if not rerank:
            return candidates[:top_k]

        # 槽位分量重排序
        reranked = self.reranker.rerank(
            candidates=candidates,
            query_slot_vectors=query_slot_vectors,
        )

        return reranked[:top_k]


class HybridSearcher:
    """
    混合搜索器

    融合多种搜索结果：
    1. 向量搜索（语义相似度）
    2. 关键字搜索（图结构）
    3. 图搜索（实体连接）

    使用 RRF (Reciprocal Rank Fusion) 融合各搜索结果。
    """

    def __init__(self, vector_db: ChromaVectorDB, fusion_k: int = 60):
        """
        Args:
            vector_db: ChromaVectorDB 实例
            fusion_k: RRF 融合参数，值越大各排名权重越平均
        """
        self.db = vector_db
        self.fusion_k = fusion_k
        self.dynamic_reranker = vector_db.dynamic_reranker

    def search(
        self,
        query_vector: np.ndarray,
        query_context: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
        mode: str = "dynamic",
        keyword_results: Optional[List[str]] = None,
        graph_results: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        混合搜索

        Args:
            query_vector: [384] 查询向量
            query_context: 查询上下文，用于动态选择重排序策略
                          {
                              "result_focused": bool,   # 是否 result 槽位主导
                              "time_sensitive": bool,   # 是否时间敏感
                              "entity_centric": bool,   # 是否实体中心
                          }
            top_k: 返回数量
            mode: 搜索模式
                - "vector": 只用向量搜索
                - "hybrid": 向量+关键字+图混合
                - "dynamic": 根据上下文动态选择
            keyword_results: 关键字搜索返回的 memory_id 列表
            graph_results: 图搜索返回的 memory_id 列表

        Returns:
            List[{"memory_id": str, "distance": float, "score": float}]
        """
        query_context = query_context or {}

        if mode == "vector":
            return self.db.search(query_vector=query_vector, top_k=top_k)

        # === 向量搜索 ===
        vector_results = self.db.search(query_vector=query_vector, top_k=top_k * 3)

        if mode == "hybrid" and (keyword_results or graph_results):
            # === RRF 融合 ===
            all_results = self._rrf_fusion(
                vector_results=vector_results,
                keyword_results=keyword_results or [],
                graph_results=graph_results or [],
                top_k=top_k,
            )

            # === 动态重排序 ===
            # 构建候选列表（包含 metadata）
            candidates_with_meta = []
            for r in all_results:
                item = self.db.get(r["memory_id"], include_vector=False)
                cand = {
                    "memory_id": r["memory_id"],
                    "distance": r.get("distance", 0),
                    "metadata": item["metadata"] if item else {},
                }
                candidates_with_meta.append(cand)

            # 使用动态重排序器（简化版：直接返回 RRF 结果）
            # 动态重排序需要 query_slot_vectors，但这里只有完整 query_vector
            # 因此只返回 RRF 融合结果
            reranked = all_results[:top_k]

            return reranked
        else:
            # 只用向量搜索，简单截取 top_k
            return vector_results[:top_k]

    def _rrf_fusion(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[str],
        graph_results: List[str],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        RRF (Reciprocal Rank Fusion) 融合

        score = Σ 1 / (k + rank_i)
        """
        scores = {}

        # 向量搜索得分
        for rank, result in enumerate(vector_results):
            mem_id = result["memory_id"]
            score = 1 / (self.fusion_k + rank + 1)
            scores[mem_id] = scores.get(mem_id, 0) + score
            # 保留 distance 信息
            if "distance" not in scores:
                scores[f"{mem_id}_distance"] = result["distance"]

        # 关键字搜索得分
        for rank, mem_id in enumerate(keyword_results):
            score = 1 / (self.fusion_k + rank + 1)
            scores[mem_id] = scores.get(mem_id, 0) + score

        # 图搜索得分
        for rank, mem_id in enumerate(graph_results):
            score = 1 / (self.fusion_k + rank + 1)
            scores[mem_id] = scores.get(mem_id, 0) + score

        # 按得分排序
        sorted_ids = sorted(
            [mid for mid in scores.keys() if not mid.endswith("_distance")],
            key=lambda mid: scores.get(mid, 0),
            reverse=True,
        )

        # 构建结果
        output = []
        for mem_id in sorted_ids[:top_k]:
            result = {
                "memory_id": mem_id,
                "score": scores[mem_id],
            }
            dist_key = f"{mem_id}_distance"
            if dist_key in scores:
                result["distance"] = scores[dist_key]
            output.append(result)

        return output
