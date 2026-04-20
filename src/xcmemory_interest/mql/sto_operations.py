"""
STO 阶段操作集（Text2Mem 借鉴）

参考: MEMU_TEXT2MEM_REFERENCE.md 第十二章

STO (STOre) 操作集用于对已存储的记忆进行管理操作：
- PROMOTE / DEMOTE: 权重调整
- EXPIRE: 过期机制
- LOCK / UNLOCK: 锁定防误删
- MERGE / SPLIT: 合并/拆分 + Lineage 追踪

Lineage 追踪字段（存储在 extra 字段）:
- lineage_parents: list[str] - 祖先记忆 ID
- lineage_children: list[str] - 后代记忆 ID
- deprecated: bool - 是否已废弃
- replaced_by: str - 被谁替代

其他 STO 字段:
- importance_weight: float - 重要性权重（默认 1.0）
- locked: bool - 是否被锁定
- expires_at: str - 过期时间（ISO 格式）
- auto_delete: bool - 过期后是否自动删除
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..basic_crud.vec_db_crud import VecDBCRUD, Memory
    from ..pyapi.core import MemorySystem


# ============================================================================
# STO Operations
# ============================================================================

class STOOperations:
    """
    STO 阶段操作集，为记忆系统提供权重调整、过期、锁定、合并/拆分能力。

    使用方式:
        # 方式 1: 直接使用
        sto = STOOperations(vec_db_crud)
        sto.promote("memory_id", 0.2)

        # 方式 2: 作为 MixIn 附加到 MemorySystem
        sto = STOOperations()
        sto.attach(memory_system)

        # 方式 3: 继承组合
        class MyMemorySystem(STOOperations, MemorySystem):
            pass
    """

    def __init__(self, vec_db: Optional["VecDBCRUD"] = None):
        """
        初始化 STO 操作集。

        Args:
            vec_db: VecDBCRUD 实例。如果为 None，则通过 attach() 附加。
        """
        self._vec_db = vec_db
        self._default_promote_delta = 0.2
        self._default_demote_delta = 0.1

    def attach(self, memory_system: "MemorySystem") -> None:
        """
        将 STO 操作的方法附加到 MemorySystem 实例。

        Args:
            memory_system: MemorySystem 实例
        """
        self._vec_db = memory_system._vec_db
        for method_name in dir(self):
            if method_name.startswith("_") or method_name == "attach":
                continue
            method = getattr(self, method_name, None)
            if callable(method):
                setattr(memory_system, method_name, method)

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _get_memory(self, memory_id: str) -> Optional["Memory"]:
        """获取记忆对象"""
        if self._vec_db is None:
            raise RuntimeError("VecDBCRUD not initialized. Call attach() or pass vec_db to __init__.")
        return self._vec_db._kv_read(memory_id)

    def _update_extra(self, memory_id: str, extra_updates: Dict[str, Any]) -> bool:
        """
        更新记忆的 extra 字段（合并更新）。

        Args:
            memory_id: 记忆 ID
            extra_updates: 要更新的 extra 字段

        Returns:
            是否更新成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        # 合并 extra
        new_extra = dict(memory.extra)
        new_extra.update(extra_updates)

        return self._vec_db.update(memory_id, extra=new_extra)

    # =========================================================================
    # PROMOTE / DEMOTE（权重调整）
    # =========================================================================

    def promote(self, memory_id: str, weight_delta: float = 0.2) -> bool:
        """
        提升记忆权重（增加 importance_weight）。

        用于强调重要记忆，使其在检索时具有更高优先级。

        Args:
            memory_id: 记忆 ID
            weight_delta: 权重增量（默认 0.2）

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        current_weight = memory.extra.get("importance_weight", 1.0)
        new_weight = current_weight + weight_delta

        return self._update_extra(memory_id, {"importance_weight": new_weight})

    def demote(self, memory_id: str, weight_delta: float = 0.1) -> bool:
        """
        降低记忆权重。

        用于淡化不重要或过时的记忆。

        Args:
            memory_id: 记忆 ID
            weight_delta: 权重减量（默认 0.1）

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        current_weight = memory.extra.get("importance_weight", 1.0)
        new_weight = max(0.0, current_weight - weight_delta)

        return self._update_extra(memory_id, {"importance_weight": new_weight})

    def get_importance_weight(self, memory_id: str) -> Optional[float]:
        """
        获取记忆的重要性权重。

        Args:
            memory_id: 记忆 ID

        Returns:
            权重值，如果记忆不存在返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("importance_weight", 1.0)

    # =========================================================================
    # EXPIRE（过期机制）
    # =========================================================================

    def expire_after(self, memory_id: str, days: int) -> bool:
        """
        设置记忆在 N 天后过期。

        过期后记忆会被标记为 deprecated，但不会立即删除。
        可配合 auto_delete=True 实现自动删除。

        Args:
            memory_id: 记忆 ID
            days: 过期天数

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        expires_at = datetime.now() + timedelta(days=days)

        extra_updates = {
            "expires_at": expires_at.isoformat(),
            "auto_delete": True,
        }

        return self._update_extra(memory_id, extra_updates)

    def expire_at(self, memory_id: str, expires_at: datetime) -> bool:
        """
        设置记忆在指定时间过期。

        Args:
            memory_id: 记忆 ID
            expires_at: 过期时间点

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        extra_updates = {
            "expires_at": expires_at.isoformat(),
            "auto_delete": True,
        }

        return self._update_extra(memory_id, extra_updates)

    def clear_expiration(self, memory_id: str) -> bool:
        """
        清除记忆的过期设置。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        extra = dict(memory.extra)
        extra.pop("expires_at", None)
        extra.pop("auto_delete", None)

        return self._vec_db.update(memory_id, extra=extra)

    def is_expired(self, memory_id: str) -> Optional[bool]:
        """
        检查记忆是否已过期。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否过期，不存在返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None

        expires_at_str = memory.extra.get("expires_at")
        if not expires_at_str:
            return False

        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now() > expires_at
        except (ValueError, TypeError):
            return False

    def get_expires_at(self, memory_id: str) -> Optional[datetime]:
        """
        获取记忆的过期时间。

        Args:
            memory_id: 记忆 ID

        Returns:
            过期时间，如果未设置返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None

        expires_at_str = memory.extra.get("expires_at")
        if not expires_at_str:
            return None

        try:
            return datetime.fromisoformat(expires_at_str)
        except (ValueError, TypeError):
            return None

    # =========================================================================
    # LOCK / UNLOCK（锁定防误删）
    # =========================================================================

    def lock(self, memory_id: str) -> bool:
        """
        锁定记忆，防止误删。

        被锁定的记忆在执行 delete() 时会抛出 PermissionError。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        return self._update_extra(memory_id, {"locked": True})

    def unlock(self, memory_id: str) -> bool:
        """
        解除记忆锁定。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否成功
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return False

        return self._update_extra(memory_id, {"locked": False})

    def is_locked(self, memory_id: str) -> Optional[bool]:
        """
        检查记忆是否被锁定。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否锁定，不存在返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("locked", False)

    # =========================================================================
    # MERGE / SPLIT（合并/拆分）+ Lineage 追踪
    # =========================================================================

    def merge(self, memory_ids: List[str], merged_content: str) -> Optional[str]:
        """
        合并多条记忆为一条，保留血缘关系。

        合并流程:
        1. 创建新的合并记忆
        2. 设置 lineage_parents 指向被合并的记忆
        3. 将被合并的记忆标记为 deprecated，并设置 replaced_by

        Args:
            memory_ids: 要合并的记忆 ID 列表
            merged_content: 合并后的记忆内容

        Returns:
            新记忆的 ID，失败返回 None
        """
        if not memory_ids or not merged_content:
            return None

        # 获取所有被合并的记忆
        memories = []
        for mid in memory_ids:
            mem = self._get_memory(mid)
            if mem is None:
                return None
            memories.append(mem)

        # 创建合并后的记忆（复用第一条记忆的 query_sentence 和 embedding）
        # 这里需要直接操作 write 方法
        primary = memories[0]
        new_id = str(uuid.uuid4())

        # 使用第一条记忆的参数创建新记忆
        # 注意：实际项目中应该调用 memory_system.write() 方法
        # 这里直接使用 VecDBCRUD 的内部方法
        from ..basic_crud.vec_db_crud import Memory
        new_memory = Memory(
            id=new_id,
            query_sentence=primary.query_sentence,
            query_embedding=primary.query_embedding.copy(),
            raw_embedding=primary.raw_embedding.copy(),
            content=merged_content,
            lifecycle=primary.lifecycle,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            extra={
                "lineage_parents": memory_ids,
                "lineage_children": [],
                "deprecated": False,
                "replaced_by": None,
            },
        )

        # 写入新记忆
        self._vec_db._kv_write(new_memory)

        # 更新被合并的记忆：标记为 deprecated，设置 replaced_by
        for mid in memory_ids:
            self._update_extra(mid, {
                "deprecated": True,
                "replaced_by": new_id,
            })

        return new_id

    def split(self, memory_id: str, split_contents: List[str]) -> Optional[List[str]]:
        """
        拆分一条记忆为多条，保留血缘关系。

        拆分流程:
        1. 获取原记忆的内容和元数据
        2. 为每个拆分内容创建新记忆
        3. 设置原记忆的 lineage_children 指向新记忆
        4. 将原记忆标记为 deprecated

        Args:
            memory_id: 要拆分的记忆 ID
            split_contents: 拆分后的记忆内容列表

        Returns:
            新记忆 ID 列表，失败返回 None
        """
        if not split_contents:
            return None

        memory = self._get_memory(memory_id)
        if memory is None:
            return None

        new_ids = []

        # 为每个拆分内容创建新记忆
        for content in split_contents:
            new_id = str(uuid.uuid4())

            from ..basic_crud.vec_db_crud import Memory
            new_memory = Memory(
                id=new_id,
                query_sentence=memory.query_sentence,
                query_embedding=memory.query_embedding.copy(),
                raw_embedding=memory.raw_embedding.copy(),
                content=content,
                lifecycle=memory.lifecycle,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                extra={
                    "lineage_parents": [memory_id],
                    "lineage_children": [],
                    "deprecated": False,
                    "replaced_by": None,
                },
            )

            self._vec_db._kv_write(new_memory)
            new_ids.append(new_id)

        # 更新原记忆：标记为 deprecated，设置 lineage_children
        self._update_extra(memory_id, {
            "deprecated": True,
            "lineage_children": new_ids,
        })

        return new_ids

    # =========================================================================
    # Lineage 查询
    # =========================================================================

    def get_lineage_parents(self, memory_id: str) -> Optional[List[str]]:
        """
        获取记忆的祖先 ID 列表。

        Args:
            memory_id: 记忆 ID

        Returns:
            祖先 ID 列表
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("lineage_parents", [])

    def get_lineage_children(self, memory_id: str) -> Optional[List[str]]:
        """
        获取记忆的后代 ID 列表。

        Args:
            memory_id: 记忆 ID

        Returns:
            后代 ID 列表
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("lineage_children", [])

    def is_deprecated(self, memory_id: str) -> Optional[bool]:
        """
        检查记忆是否已废弃。

        Args:
            memory_id: 记忆 ID

        Returns:
            是否废弃，不存在返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("deprecated", False)

    def get_replaced_by(self, memory_id: str) -> Optional[str]:
        """
        获取替代该记忆的新记忆 ID。

        Args:
            memory_id: 记忆 ID

        Returns:
            替代记忆的 ID，如果未被替代返回 None
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None
        return memory.extra.get("replaced_by")

    def get_lineage_chain(self, memory_id: str, max_depth: int = 10) -> Dict[str, Any]:
        """
        获取记忆的血缘链。

        Args:
            memory_id: 记忆 ID
            max_depth: 最大追溯深度

        Returns:
            包含血缘信息的字典
        """
        chain = {
            "id": memory_id,
            "parents": [],
            "children": [],
            "deprecated": False,
            "replaced_by": None,
        }

        visited = set()
        queue = [(memory_id, 0)]

        while queue:
            mid, depth = queue.pop(0)
            if mid in visited or depth > max_depth:
                continue
            visited.add(mid)

            memory = self._get_memory(mid)
            if memory is None:
                continue

            if depth == 0:
                chain["deprecated"] = memory.extra.get("deprecated", False)
                chain["replaced_by"] = memory.extra.get("replaced_by")

            # 追溯父母
            parents = memory.extra.get("lineage_parents", [])
            for parent_id in parents:
                if parent_id not in visited:
                    chain["parents"].append(parent_id)
                    queue.append((parent_id, depth + 1))

            # 追溯孩子
            children = memory.extra.get("lineage_children", [])
            for child_id in children:
                if child_id not in visited:
                    chain["children"].append(child_id)
                    queue.append((child_id, depth + 1))

        return chain

    # =========================================================================
    # DELETE（带锁定检查）
    # =========================================================================

    def delete(self, memory_id: str, force: bool = False) -> bool:
        """
        删除记忆，如果记忆被锁定则拒绝删除。

        Args:
            memory_id: 记忆 ID
            force: 是否强制删除（忽略锁定）

        Returns:
            是否删除成功

        Raises:
            PermissionError: 如果记忆被锁定且 force=False
        """
        if not force:
            locked = self.is_locked(memory_id)
            if locked:
                raise PermissionError(f"Memory {memory_id} is locked")

        return self._vec_db.delete(memory_id)

    # =========================================================================
    # 批量操作
    # =========================================================================

    def batch_promote(self, memory_ids: List[str], weight_delta: float = 0.2) -> List[str]:
        """
        批量提升记忆权重。

        Args:
            memory_ids: 记忆 ID 列表
            weight_delta: 权重增量

        Returns:
            成功更新的记忆 ID 列表
        """
        succeeded = []
        for mid in memory_ids:
            if self.promote(mid, weight_delta):
                succeeded.append(mid)
        return succeeded

    def batch_demote(self, memory_ids: List[str], weight_delta: float = 0.1) -> List[str]:
        """
        批量降低记忆权重。

        Args:
            memory_ids: 记忆 ID 列表
            weight_delta: 权重减量

        Returns:
            成功更新的记忆 ID 列表
        """
        succeeded = []
        for mid in memory_ids:
            if self.demote(mid, weight_delta):
                succeeded.append(mid)
        return succeeded

    def batch_lock(self, memory_ids: List[str]) -> List[str]:
        """
        批量锁定记忆。

        Args:
            memory_ids: 记忆 ID 列表

        Returns:
            成功锁定的记忆 ID 列表
        """
        succeeded = []
        for mid in memory_ids:
            if self.lock(mid):
                succeeded.append(mid)
        return succeeded

    def batch_unlock(self, memory_ids: List[str]) -> List[str]:
        """
        批量解除锁定。

        Args:
            memory_ids: 记忆 ID 列表

        Returns:
            成功解除的记忆 ID 列表
        """
        succeeded = []
        for mid in memory_ids:
            if self.unlock(mid):
                succeeded.append(mid)
        return succeeded

    def get_sto_metadata(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """
        获取记忆的完整 STO 元数据。

        Args:
            memory_id: 记忆 ID

        Returns:
            STO 元数据字典
        """
        memory = self._get_memory(memory_id)
        if memory is None:
            return None

        return {
            "importance_weight": memory.extra.get("importance_weight", 1.0),
            "locked": memory.extra.get("locked", False),
            "expires_at": memory.extra.get("expires_at"),
            "auto_delete": memory.extra.get("auto_delete", False),
            "is_expired": self.is_expired(memory_id),
            "lineage_parents": memory.extra.get("lineage_parents", []),
            "lineage_children": memory.extra.get("lineage_children", []),
            "deprecated": memory.extra.get("deprecated", False),
            "replaced_by": memory.extra.get("replaced_by"),
        }


# ============================================================================
# STO MixIn
# ============================================================================

class STOMixIn(STOOperations):
    """
    STO 操作 MixIn 类，可与其他 MemorySystem 组合使用。

    使用方式:
        class MyMemorySystem(STOMixIn, MemorySystem):
            pass
    """

    def __init__(self):
        super().__init__()
