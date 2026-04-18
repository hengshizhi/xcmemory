"""
GraphExplorer - 记忆图高级探索

提供基于图的记忆发现和分析能力。
"""

from typing import Dict, List, Set, Optional, Tuple, Any
from dataclasses import dataclass
from collections import defaultdict, deque
import heapq

from .graph import MemoryGraph, GraphSearchResult


@dataclass
class MemoryCluster:
    """记忆簇（强连通分量）"""
    cluster_id: str
    memory_ids: List[str]
    shared_patterns: Dict[str, int]  # 槽位值出现频率
    avg_lifecycle: float
    size: int


class GraphExplorer:
    """
    记忆图探索器

    提供高级图查询和分析功能：
    - 基于共享槽位发现相似记忆群
    - 记忆网络结构分析
    - 路径发现和关系追溯
    """

    def __init__(self, vec_db):
        """
        初始化图探索器

        Args:
            vec_db: VecDBCRUD 实例
        """
        self._graph = MemoryGraph(vec_db)
        self._vec_db = vec_db
        self._slot_names = vec_db.SLOT_NAMES

    # =========================================================================
    # 记忆群发现
    # =========================================================================

    def find_similar_memories(
        self,
        memory_id: str,
        top_k: int = 10,
        min_shared_slots: int = 1,
    ) -> List[GraphSearchResult]:
        """
        找到与指定记忆相似的其他记忆（基于图连接）

        Args:
            memory_id: 起始记忆 ID
            top_k: 返回数量
            min_shared_slots: 最少共享槽位数

        Returns:
            按连接强度排序的相似记忆列表
        """
        neighbors = self._graph.get_neighbors(memory_id, min_shared_slots=min_shared_slots)

        # 按共享槽位数排序
        neighbors.sort(key=lambda x: -len(x.shared_slots))

        return neighbors[:top_k]

    def find_memory_clusters(
        self,
        min_cluster_size: int = 2,
        min_shared_slots: int = 2,
    ) -> List[MemoryCluster]:
        """
        发现记忆簇（通过共享槽位自然聚类）

        使用连通分量算法找簇，然后分析簇特征。

        Args:
            min_cluster_size: 最小簇大小
            min_shared_slots: 最少共享槽位数（连接阈值）

        Returns:
            MemoryCluster 列表
        """
        # 获取所有记忆
        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT id FROM memories")
        all_memory_ids = [row[0] for row in cur.fetchall()]

        visited = set()
        clusters = []

        for memory_id in all_memory_ids:
            if memory_id in visited:
                continue

            # 获取该记忆的连通分量
            component = self._graph.get_connected_component(
                memory_id,
                min_shared_slots=min_shared_slots,
            )

            # 过滤太小或已被访问的
            component_ids = [r.memory_id for r in component]
            unvisited = [mid for mid in component_ids if mid not in visited]

            if len(unvisited) >= min_cluster_size:
                # 构建簇
                cluster_id = f"cluster_{len(clusters)}"
                clusters.append(self._build_cluster(unvisited))

                for mid in unvisited:
                    visited.add(mid)

        return clusters

    def _build_cluster(self, memory_ids: List[str]) -> MemoryCluster:
        """构建记忆簇"""
        cur = self._vec_db._conn.cursor()

        # 收集槽位值频率
        slot_value_counts = defaultdict(lambda: defaultdict(int))
        lifecycles = []

        for mid in memory_ids:
            cur.execute("SELECT * FROM slot_value_index WHERE memory_id = ?", (mid,))
            row = cur.fetchone()
            if row:
                for slot in self._slot_names:
                    val = row[f"{slot}_value"]
                    if val:
                        slot_value_counts[slot][val] += 1

            cur.execute("SELECT lifecycle FROM memories WHERE id = ?", (mid,))
            r = cur.fetchone()
            if r:
                lifecycles.append(r["lifecycle"])

        # 构建共享模式（出现频率 >= 2 的值）
        shared_patterns = {}
        for slot, value_counts in slot_value_counts.items():
            for value, count in value_counts.items():
                if count >= 2:
                    if slot not in shared_patterns:
                        shared_patterns[slot] = {}
                    shared_patterns[slot][value] = count

        avg_lc = sum(lifecycles) / len(lifecycles) if lifecycles else 0

        return MemoryCluster(
            cluster_id=f"cluster_{len(memory_ids)}",
            memory_ids=memory_ids,
            shared_patterns=dict(shared_patterns),
            avg_lifecycle=avg_lc,
            size=len(memory_ids),
        )

    # =========================================================================
    # 关系追溯
    # =========================================================================

    def trace_memory_evolution(
        self,
        memory_id: str,
        max_depth: int = 3,
    ) -> List[GraphSearchResult]:
        """
        追溯记忆的"进化链"

        找到与给定记忆在同一主题上扩展/相关的记忆序列。

        Args:
            memory_id: 起始记忆 ID
            max_depth: 最大深度

        Returns:
            进化链上的记忆列表
        """
        # 找到所有关联记忆
        connected = self._graph.get_connected_component(
            memory_id,
            min_shared_slots=1,
        )

        # 按距离分组排序
        by_distance = defaultdict(list)
        for result in connected:
            if result.distance <= max_depth:
                by_distance[result.distance].append(result)

        # 构建进化序列
        evolution = []
        seen = {memory_id}

        # 距离 0: 起始记忆本身
        evolution.append(GraphSearchResult(
            memory_id=memory_id,
            distance=0,
            shared_slots={},
            path=[memory_id],
        ))

        # 逐层扩展
        for dist in range(1, max_depth + 1):
            for result in sorted(by_distance[dist], key=lambda x: -len(x.shared_slots)):
                if result.memory_id not in seen:
                    evolution.append(result)
                    seen.add(result.memory_id)

                    if len(evolution) >= 20:  # 限制结果数
                        return evolution

        return evolution

    def find_bridging_memories(
        self,
        memory_id_a: str,
        memory_id_b: str,
        max_depth: int = 3,
    ) -> Optional[List[str]]:
        """
        找到连接两条记忆的"桥梁"记忆

        如果 A 和 B 不直接相连，通过中间记忆连接它们。

        Args:
            memory_id_a: 记忆 A
            memory_id_b: 记忆 B
            max_depth: 最大深度

        Returns:
            桥梁记忆 ID 列表 + 路径，或 None
        """
        path = self._graph.find_path(
            memory_id_a,
            memory_id_b,
            max_depth=max_depth,
            min_shared_slots=1,
        )
        return path

    # =========================================================================
    # 网络分析
    # =========================================================================

    def get_memory_importance(
        self,
        memory_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        计算记忆的重要性分数（基于连接数）

        Args:
            memory_id: 指定记忆的 ID（不指定则计算所有）

        Returns:
            {memory_id: importance_score}
        """
        # 统计每条记忆的连接数
        connection_counts = defaultdict(int)

        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT id FROM memories")
        all_ids = [row[0] for row in cur.fetchall()]

        for mid in all_ids:
            neighbors = self._graph.get_neighbors(mid, min_shared_slots=1)
            connection_counts[mid] = len(neighbors)

        if memory_id:
            # 返回指定记忆及其直接邻居的分数
            neighbors = self._graph.get_neighbors(memory_id, min_shared_slots=1)
            result = {memory_id: float(connection_counts[memory_id])}
            for n in neighbors:
                result[n.memory_id] = float(connection_counts[n.memory_id])
            return result

        # 返回所有记忆的分数
        return {mid: float(count) for mid, count in connection_counts.items()}

    def find_hub_memories(
        self,
        top_k: int = 10,
    ) -> List[Tuple[str, int]]:
        """
        找到"枢纽"记忆（连接数最多的记忆）

        Args:
            top_k: 返回数量

        Returns:
            [(memory_id, connection_count), ...]
        """
        importance = self.get_memory_importance()
        sorted_items = sorted(importance.items(), key=lambda x: -x[1])
        return sorted_items[:top_k]

    # =========================================================================
    # 批量查询
    # =========================================================================

    def batch_find_similar(
        self,
        memory_ids: List[str],
        top_k_per: int = 5,
        min_shared_slots: int = 1,
    ) -> Dict[str, List[GraphSearchResult]]:
        """
        批量查找相似记忆

        Args:
            memory_ids: 记忆 ID 列表
            top_k_per: 每条记忆返回的数量
            min_shared_slots: 最少共享槽位数

        Returns:
            {memory_id: [similar_result, ...], ...}
        """
        results = {}
        seen = set()

        for mid in memory_ids:
            similar = self.find_similar_memories(
                mid,
                top_k=top_k_per,
                min_shared_slots=min_shared_slots,
            )
            # 去重
            unique = [r for r in similar if r.memory_id not in seen]
            seen.update(r.memory_id for r in unique)
            results[mid] = unique

        return results