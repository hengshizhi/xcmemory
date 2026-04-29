"""
WriteCache - 全异步写入缓存

write() 只记录原始输入，立即返回 ID（<1ms）。
后台线程完成：生命周期计算 → 向量编码 → 批量落盘 ChromaDB + SQLite + 索引。
读取时只返回已处理完成的记忆。
"""

import threading
import time
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .core import Memory, MemorySystem

logger = logging.getLogger("xcmemory.write_cache")


class WriteCache:
    """全异步写入缓存。"""

    def __init__(
        self,
        flush_interval: float = 0.5,
        ttl_after_flush: float = 30.0,
    ):
        self._flush_interval = flush_interval
        self._ttl_after_flush = ttl_after_flush

        # 原始输入队列（待处理）
        self._raw_pending: List[Dict[str, Any]] = []
        # memory_id → (Memory, flush_time) — 只有已完成的记忆
        self._by_id: Dict[str, tuple] = {}

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 注入的后端组件
        self._system: Optional["MemorySystem"] = None
        self._vec_db = None
        self._time_index = None
        self._slot_index = None
        self._lifecycle_mgr = None
        self._started = False

    def bind(self, system: "MemorySystem"):
        """绑定 MemorySystem（包含所有后端组件）。"""
        self._system = system
        self._vec_db = system._vec_db
        self._time_index = system._time_index
        self._slot_index = system._slot_index
        self._lifecycle_mgr = system._lifecycle_mgr

    def start(self):
        if self._started:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started = True

    def stop(self):
        if not self._started:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30.0)
        self._process_all()
        self._started = False

    # ---- 写入（异步） ----

    def submit(
        self,
        query_sentence: str,
        content: str,
        reference_duration: Optional[int] = None,
        scene_word: Optional[str] = None,
    ) -> str:
        """提交写入请求，立即返回 memory_id（不等待处理完成）。"""
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._raw_pending.append({
                "query_sentence": query_sentence,
                "content": content,
                "reference_duration": reference_duration,
                "scene_word": scene_word,
                "memory_id": memory_id,
                "created_at": datetime.now(),
            })
        return memory_id

    # ---- 读取 ----

    def read(self, memory_id: str) -> Optional["Memory"]:
        """读取已完成的记忆。未处理完或不存在返回 None。"""
        with self._lock:
            entry = self._by_id.get(memory_id)
        if entry is not None:
            return entry[0]
        if self._vec_db is not None:
            return self._vec_db._kv_read(memory_id)
        return None

    # ---- 搜索补充 ----

    def search_supplement(
        self,
        query_vector: np.ndarray,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """在已完成的缓存记忆中搜索。"""
        with self._lock:
            entries = list(self._by_id.items())
        results = []
        for mid, (mem, _) in entries:
            vec = getattr(mem, "query_embedding", None)
            if vec is None or vec.shape[0] != query_vector.shape[0]:
                continue
            dist = float(np.linalg.norm(query_vector - vec))
            results.append({"memory_id": mid, "distance": dist, "memory": mem})
        results.sort(key=lambda r: r["distance"])
        return results[:top_k]

    def pending_count(self) -> int:
        """待处理队列长度。"""
        with self._lock:
            return len(self._raw_pending)

    # ---- 后台处理 ----

    def _run(self):
        while not self._stop_event.wait(self._flush_interval):
            self._process_all()

    def _process_all(self):
        raw_list = []
        with self._lock:
            if not self._raw_pending:
                return
            raw_list = list(self._raw_pending)
            self._raw_pending.clear()

        try:
            memories = []
            slot_vecs_list = []
            slot_values_list = []

            for raw in raw_list:
                try:
                    mem, sv, svv = self._process_one(raw)
                    if mem is not None:
                        memories.append(mem)
                        slot_vecs_list.append(sv)
                        slot_values_list.append(svv)
                except Exception:
                    logger.exception("Failed to process pending write: %s", raw.get("memory_id", "?"))

            if not memories:
                return

            # 批量写入 ChromaDB + SQLite
            items = [{
                "query_sentence": m.query_sentence,
                "content": m.content,
                "lifecycle": m.lifecycle,
            } for m in memories]
            cache_ids = [m.id for m in memories]
            if self._vec_db is not None:
                self._vec_db.write_batch(items, memory_ids=cache_ids)

            # 时间索引
            if self._time_index is not None:
                for m in memories:
                    parts = self._vec_db._parse_query_sentence(m.query_sentence)
                    scene = parts[0] if parts else ""
                    if scene:
                        self._time_index.add(memory_id=m.id, time_word=scene, created_at=m.created_at)

            # 槽位索引
            if self._slot_index is not None:
                for i, m in enumerate(memories):
                    self._slot_index.add(
                        memory_id=m.id,
                        slot_vectors=slot_vecs_list[i],
                        slot_values=slot_values_list[i],
                    )

            # 写入完成，加入可读缓存
            now = time.time()
            with self._lock:
                for m in memories:
                    self._by_id[m.id] = (m, now)
                self._cleanup_expired()

        except Exception:
            logger.exception("WriteCache batch process failed")

    def _process_one(self, raw: Dict[str, Any]):
        """处理单条原始输入：生命周期 + 编码，返回 (Memory, slot_vecs, slot_values)。"""
        query_sentence = raw["query_sentence"]
        content = raw.get("content", "")
        reference_duration = raw.get("reference_duration")
        memory_id = raw["memory_id"]
        created_at = raw["created_at"]

        # 生命周期计算
        parts = self._vec_db._parse_query_sentence(query_sentence)
        slot_dict = {
            "scene": parts[0], "subject": parts[1], "action": parts[2],
            "object": parts[3], "purpose": parts[4], "result": parts[5],
        }
        slot_dict = {k: v for k, v in slot_dict.items() if v}

        if self._lifecycle_mgr is not None:
            lifecycle = self._lifecycle_mgr.decide_new_lifecycle(
                query_slots=slot_dict,
                reference_duration=reference_duration if reference_duration is not None else 86400,
            )
        else:
            lifecycle = reference_duration if reference_duration is not None else 86400

        # 向量编码
        slots = self._vec_db._slots_from_sentence(query_sentence)
        interest_vec = self._vec_db.pipeline.encode(slots, use_raw=False, normalize=True)
        raw_vec = self._vec_db.pipeline.encode(slots, use_raw=True, normalize=True)
        slot_vecs = self._vec_db.pipeline.get_slot_vectors(slots)
        slot_values = {name: parts[i] for i, name in enumerate(self._vec_db.SLOT_NAMES)}

        # 构建 Memory（如果系统配置了兴趣模式用 interest_vec，否则用 raw_vec）
        if self._system is not None and getattr(self._system, "enable_interest_mode", False):
            full_vec = interest_vec
        else:
            full_vec = raw_vec

        from .core import Memory as MemClass
        memory = MemClass(
            id=memory_id,
            query_sentence=query_sentence,
            query_embedding=full_vec,
            raw_embedding=raw_vec,
            content=content,
            lifecycle=lifecycle,
            created_at=created_at,
            updated_at=created_at,
        )

        return memory, slot_vecs, slot_values

    def _cleanup_expired(self):
        now = time.time()
        expired = []
        for mid, (_, flush_time) in self._by_id.items():
            if flush_time is not None and (now - flush_time) > self._ttl_after_flush:
                expired.append(mid)
        for mid in expired:
            del self._by_id[mid]
