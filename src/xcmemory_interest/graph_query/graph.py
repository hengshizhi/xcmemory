"""
MemoryGraph - 记忆图构建与基础查询
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple, Any
from collections import defaultdict, deque


@dataclass
class GraphSearchResult:
    """图搜索结果"""
    memory_id: str
    distance: int  # 跳数（0=起始节点）
    shared_slots: Dict[str, str]  # 与起始记忆共享的槽位值
    path: List[str]  # 从起始节点到该节点的路径


class MemoryGraph:
    """
    记忆图构建器

    基于槽位值索引构建记忆之间的关联关系。
    图是隐式的（不显式存储边），而是通过槽位值索引动态构建。

    关联定义：两个记忆在同一槽位有相同的非空值，则认为它们在该槽位上相连。
    """

    def __init__(self, vec_db):
        """
        初始化记忆图

        Args:
            vec_db: VecDBCRUD 实例（用于访问 slot_value_index）
        """
        self._vec_db = vec_db
        self._slot_names = vec_db.SLOT_NAMES  # ["time", "subject", "action", "object", "purpose", "result"]

    # =========================================================================
    # 图构建（基于索引）
    # =========================================================================

    def _build_value_to_memories(self) -> Dict[Tuple[str, str], List[str]]:
        """
        构建"槽位值 → 记忆ID列表"的反向索引

        Returns:
            {(slot_name, value): [memory_id, ...], ...}
        """
        value_to_memories = defaultdict(list)

        for slot in self._slot_names:
            col_name = f"{slot}_value"
            cur = self._vec_db._conn.cursor()
            cur.execute(f"SELECT memory_id, {col_name} FROM slot_value_index WHERE {col_name} IS NOT NULL AND {col_name} != ''")
            for row in cur.fetchall():
                memory_id, value = row
                if value:
                    value_to_memories[(slot, value)].append(memory_id)

        return value_to_memories

    def get_neighbors(
        self,
        memory_id: str,
        max_distance: int = 1,
        min_shared_slots: int = 1,
    ) -> List[GraphSearchResult]:
        """
        获取与指定记忆相邻的节点（直接关联的记忆）

        Args:
            memory_id: 起始记忆 ID
            max_distance: 最大跳数（目前只支持 1）
            min_shared_slots: 最少共享槽位数

        Returns:
            GraphSearchResult 列表
        """
        # 获取该记忆的槽位值
        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT * FROM slot_value_index WHERE memory_id = ?", (memory_id,))
        row = cur.fetchone()
        if not row:
            return []

        # 提取该记忆的槽位值
        slot_values = {
            "time": row["time_value"],
            "subject": row["subject_value"],
            "action": row["action_value"],
            "object": row["object_value"],
            "purpose": row["purpose_value"],
            "result": row["result_value"],
        }

        # 构建"槽位值 → 记忆ID"的反向索引
        value_to_memories = self._build_value_to_memories()

        # 收集所有相邻记忆
        neighbor_ids = set()
        shared_slot_map = {}  # memory_id -> {slot: value}

        for slot, value in slot_values.items():
            if not value:
                continue
            key = (slot, value)
            if key in value_to_memories:
                for neighbor_id in value_to_memories[key]:
                    if neighbor_id != memory_id:
                        neighbor_ids.add(neighbor_id)
                        if neighbor_id not in shared_slot_map:
                            shared_slot_map[neighbor_id] = {}
                        shared_slot_map[neighbor_id][slot] = value

        # 过滤共享槽位数不足的
        results = []
        for neighbor_id, shared in shared_slot_map.items():
            if len(shared) >= min_shared_slots:
                results.append(GraphSearchResult(
                    memory_id=neighbor_id,
                    distance=1,
                    shared_slots=shared,
                    path=[memory_id, neighbor_id],
                ))

        return results

    def get_connected_component(
        self,
        memory_id: str,
        min_shared_slots: int = 1,
    ) -> List[GraphSearchResult]:
        """
        获取与指定记忆相连通的所有记忆（连通分量）

        使用 BFS 遍历

        Args:
            memory_id: 起始记忆 ID
            min_shared_slots: 最少共享槽位数

        Returns:
            GraphSearchResult 列表（包含起始记忆自身）
        """
        visited = {memory_id: GraphSearchResult(
            memory_id=memory_id,
            distance=0,
            shared_slots={},
            path=[memory_id],
        )}

        queue = deque([memory_id])

        while queue:
            current = queue.popleft()
            neighbors = self.get_neighbors(current, max_distance=1, min_shared_slots=min_shared_slots)

            for neighbor in neighbors:
                if neighbor.memory_id not in visited:
                    # 更新距离和路径
                    neighbor.distance = visited[current].distance + 1
                    neighbor.path = visited[current].path + [neighbor.memory_id]
                    visited[neighbor.memory_id] = neighbor
                    queue.append(neighbor.memory_id)

        return list(visited.values())

    def find_path(
        self,
        from_memory_id: str,
        to_memory_id: str,
        max_depth: int = 3,
        min_shared_slots: int = 1,
    ) -> Optional[List[str]]:
        """
        查找两条记忆之间的最短路径

        使用 BFS

        Args:
            from_memory_id: 起始记忆 ID
            to_memory_id: 目标记忆 ID
            max_depth: 最大深度
            min_shared_slots: 最少共享槽位数

        Returns:
            路径（memory_id 列表），找不到返回 None
        """
        if from_memory_id == to_memory_id:
            return [from_memory_id]

        visited = {from_memory_id}
        queue = deque([(from_memory_id, [from_memory_id])])

        while queue:
            current, path = queue.popleft()

            if len(path) > max_depth:
                continue

            neighbors = self.get_neighbors(current, max_distance=1, min_shared_slots=min_shared_slots)

            for neighbor in neighbors:
                if neighbor.memory_id == to_memory_id:
                    return path + [to_memory_id]

                if neighbor.memory_id not in visited:
                    visited.add(neighbor.memory_id)
                    queue.append((neighbor.memory_id, path + [neighbor.memory_id]))

        return None

    def find_memories_by_value_chain(
        self,
        start_memory_id: str,
        value_slots: List[str],
        max_depth: int = 3,
    ) -> List[GraphSearchResult]:
        """
        沿槽位值链扩展搜索

        给定一个起始记忆和一组槽位，按顺序在每个槽位上找有相同值的其他记忆。

        例如：
          start_memory: <平时><我><学习><Python>...
          value_slots: ["action", "object"]
          → 先找 action=学习 的其他记忆，再从这些记忆中找 object 与起始记忆相同的

        Args:
            start_memory_id: 起始记忆 ID
            value_slots: 要追踪的槽位顺序
            max_depth: 最大深度

        Returns:
            找到的记忆列表
        """
        if not value_slots:
            return []

        # 获取起始记忆的槽位值
        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT * FROM slot_value_index WHERE memory_id = ?", (start_memory_id,))
        row = cur.fetchone()
        if not row:
            return []

        start_values = {
            "time": row["time_value"],
            "subject": row["subject_value"],
            "action": row["action_value"],
            "object": row["object_value"],
            "purpose": row["purpose_value"],
            "result": row["result_value"],
        }

        # 验证槽位有效性
        valid_slots = [s for s in value_slots if s in self._slot_names and start_values.get(s)]
        if len(valid_slots) != len(value_slots):
            # 有无效槽位或起始值
            return []

        results = []
        current_candidates = {start_memory_id}

        for slot in valid_slots:
            target_value = start_values[slot]
            next_candidates = set()

            for candidate_id in current_candidates:
                # 获取该记忆在该槽位的值
                cur.execute(f"SELECT {slot}_value FROM slot_value_index WHERE memory_id = ?", (candidate_id,))
                row = cur.fetchone()
                if not row or row[f"{slot}_value"] != target_value:
                    continue

                # 找所有在该槽位有相同值的记忆
                cur.execute(f"SELECT memory_id FROM slot_value_index WHERE {slot}_value = ?", (target_value,))
                for r in cur.fetchall():
                    neighbor_id = r["memory_id"]
                    if neighbor_id != candidate_id:
                        next_candidates.add(neighbor_id)
                        if neighbor_id not in [res.memory_id for res in results]:
                            results.append(GraphSearchResult(
                                memory_id=neighbor_id,
                                distance=1,
                                shared_slots={slot: target_value},
                                path=[start_memory_id, neighbor_id],
                            ))

            current_candidates = next_candidates
            if not current_candidates:
                break

        return results

    def get_connection_strength(
        self,
        memory_id_a: str,
        memory_id_b: str,
    ) -> Dict[str, Any]:
        """
        计算两条记忆之间的连接强度

        Args:
            memory_id_a: 记忆 A 的 ID
            memory_id_b: 记忆 B 的 ID

        Returns:
            {
                "shared_slots": {"slot": value, ...},  # 共享的槽位
                "strength": 0.0~1.0,  # 连接强度（共享槽位数/总非空槽位数）
                "is_connected": bool,
            }
        """
        cur = self._vec_db._conn.cursor()
        cur.execute("SELECT * FROM slot_value_index WHERE memory_id = ?", (memory_id_a,))
        row_a = cur.fetchone()
        cur.execute("SELECT * FROM slot_value_index WHERE memory_id = ?", (memory_id_b,))
        row_b = cur.fetchone()

        if not row_a or not row_b:
            return {"shared_slots": {}, "strength": 0.0, "is_connected": False}

        # 提取槽位值
        values_a = {
            "time": row_a["time_value"],
            "subject": row_a["subject_value"],
            "action": row_a["action_value"],
            "object": row_a["object_value"],
            "purpose": row_a["purpose_value"],
            "result": row_a["result_value"],
        }
        values_b = {
            "time": row_b["time_value"],
            "subject": row_b["subject_value"],
            "action": row_b["action_value"],
            "object": row_b["object_value"],
            "purpose": row_b["purpose_value"],
            "result": row_b["result_value"],
        }

        # 找共享槽位
        shared_slots = {}
        total_non_empty = 0
        for slot in self._slot_names:
            if values_a[slot] or values_b[slot]:
                total_non_empty += 1
                if values_a[slot] and values_b[slot] and values_a[slot] == values_b[slot]:
                    shared_slots[slot] = values_a[slot]

        strength = len(shared_slots) / total_non_empty if total_non_empty > 0 else 0.0

        return {
            "shared_slots": shared_slots,
            "strength": strength,
            "is_connected": len(shared_slots) > 0,
        }

    @property
    def _conn(self):
        """获取数据库连接"""
        return self._vec_db._conn