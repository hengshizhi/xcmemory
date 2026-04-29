"""
WriteCache - 内存写缓存 + 后台批量刷盘

写入时立即存入内存缓存并返回（<1ms），后台线程定时批量刷入 ChromaDB + SQLite。
读取时优先查缓存，搜索时补充缓存向量比对。
"""

import threading
import time
import logging
from typing import Dict, List, Optional, Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .core import Memory

logger = logging.getLogger("xcmemory.write_cache")


class WriteCache:
    """异步写入缓存：写操作立即返回，后台批量落盘。"""

    def __init__(
        self,
        flush_interval: float = 1.0,
        flush_batch_size: int = 100,
        ttl_after_flush: float = 10.0,
    ):
        """
        Args:
            flush_interval: 后台刷盘间隔（秒）
            flush_batch_size: 攒够多少条立即刷盘（0 表示只用定时）
            ttl_after_flush: 刷盘后缓存保留时间（秒），用于 read-by-id 加速
        """
        self._flush_interval = flush_interval
        self._flush_batch_size = flush_batch_size
        self._ttl_after_flush = ttl_after_flush

        # 待刷盘数据
        self._pending: List["Memory"] = []
        # memory_id → (Memory, flush_time or None)
        self._by_id: Dict[str, tuple] = {}
        # memory_id → {slot_vecs, slot_values}
        self._slot_data: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._vec_db = None  # 由 MemorySystem 注入
        self._time_index = None
        self._slot_index = None
        self._started = False

    # ---- 生命周期 ----

    def bind(self, vec_db, time_index=None, slot_index=None):
        """绑定后端存储（由 MemorySystem 在 initialize 时调用）。"""
        self._vec_db = vec_db
        self._time_index = time_index
        self._slot_index = slot_index

    def start(self):
        """启动后台刷盘线程。"""
        if self._started:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        self._started = True

    def stop(self):
        """停止后台线程并等待最后一次刷盘。"""
        if not self._started:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30.0)
        self._flush_all()
        self._started = False

    # ---- 写入 ----

    def write(self, memory: "Memory", slot_vecs: Dict[str, np.ndarray], slot_values: Dict[str, str]):
        """写入内存缓存，立即返回。"""
        with self._lock:
            self._by_id[memory.id] = (memory, None)  # flush_time=None 表示未刷盘
            self._slot_data[memory.id] = {"vecs": slot_vecs, "values": slot_values}
            self._pending.append(memory)

        # 如果攒够了阈值，唤醒后台线程立即刷盘
        if self._flush_batch_size > 0 and len(self._pending) >= self._flush_batch_size:
            pass  # 后台线程会在 _flush_loop 中检测到

    # ---- 读取 ----

    def read(self, memory_id: str) -> Optional["Memory"]:
        """读取记忆（缓存优先）。"""
        with self._lock:
            entry = self._by_id.get(memory_id)
        if entry is not None:
            return entry[0]

        # 未在缓存，从磁盘读取
        if self._vec_db is not None:
            return self._vec_db._kv_read(memory_id)
        return None

    # ---- 搜索补充 ----

    def search_supplement(
        self,
        query_vector: np.ndarray,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        在未刷盘缓存中执行向量搜索，返回补充结果。
        返回格式：[{memory_id, distance, memory}, ...]
        """
        with self._lock:
            # 只搜索未刷盘的条目
            candidates = [
                (mid, entry[0])
                for mid, entry in self._by_id.items()
                if entry[1] is None  # 未刷盘
            ]
            if not candidates:
                return []

        results = []
        for mid, mem in candidates:
            vec = getattr(mem, "query_embedding", None)
            if vec is None or vec.shape[0] != query_vector.shape[0]:
                continue
            dist = float(np.linalg.norm(query_vector - vec))
            results.append({"memory_id": mid, "distance": dist, "memory": mem})

        results.sort(key=lambda r: r["distance"])
        return results[:top_k]

    # ---- 后台刷盘 ----

    def _flush_loop(self):
        """后台刷盘循环。"""
        while not self._stop_event.wait(self._flush_interval):
            self._flush_all()

    def _flush_all(self):
        """立即将所有待刷盘数据批量落盘。"""
        with self._lock:
            pending = list(self._pending)
            if not pending:
                return
            self._pending.clear()

        try:
            # 收集待刷盘的 IDs 和数据
            cache_ids = []
            items = []
            for mem in pending:
                cache_ids.append(mem.id)
                items.append({
                    "query_sentence": mem.query_sentence,
                    "content": mem.content,
                    "lifecycle": mem.lifecycle,
                })

            # 1. 批量写入 ChromaDB + SQLite（传入预分配的 ID）
            if self._vec_db is not None and items:
                self._vec_db.write_batch(items, memory_ids=cache_ids)

            # 2. 批量写入时间索引
            if self._time_index is not None:
                for mem in pending:
                    parts = self._vec_db._parse_query_sentence(mem.query_sentence) if self._vec_db else ["", "", "", "", "", ""]
                    scene_word = parts[0] if parts else ""
                    if scene_word:
                        self._time_index.add(
                            memory_id=mem.id,
                            time_word=scene_word,
                            created_at=mem.created_at,
                        )

            # 3. 批量写入槽位索引
            if self._slot_index is not None:
                for mem in pending:
                    sd = self._slot_data.get(mem.id)
                    if sd:
                        self._slot_index.add(
                            memory_id=mem.id,
                            slot_vectors=sd["vecs"],
                            slot_values=sd["values"],
                        )

            # 4. 标记为已刷盘
            now = time.time()
            with self._lock:
                for mem in pending:
                    self._by_id[mem.id] = (mem, now)  # 记录刷盘时间

            # 5. 清理过期的缓存条目
            self._cleanup_expired()

        except Exception:
            logger.exception("WriteCache flush failed")

    def _cleanup_expired(self):
        """清理超过 TTL 的缓存条目。"""
        now = time.time()
        expired = []
        for mid, (_, flush_time) in self._by_id.items():
            if flush_time is not None and (now - flush_time) > self._ttl_after_flush:
                expired.append(mid)
        for mid in expired:
            del self._by_id[mid]
            self._slot_data.pop(mid, None)
