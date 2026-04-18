"""
KVDatabase - 基于 LMDB 的键值存储
"""

import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Iterator, Optional

import lmdb


class KVDatabase:
    """
    KV 数据库接口

    基于 LMDB 实现，提供高性能的键值存储能力。

    特点：
        - 嵌入式，B+树，内存映射
        - 读性能极快
        - 支持多线程并发读
        - 单线程写（或通过 write_lock 控制的线程安全写入）

    目录结构：
        persist_directory/
        └── kv_{db_name}.lmdb/

    TTL 实现：
        - 值中存储 expire_at 时间戳
        - 读取时检查是否过期，过期返回 default 并删除
    """

    # LMDB map_size：默认 100MB，单个数据库够用
    DEFAULT_MAP_SIZE = 100 * 1024 * 1024  # 100MB

    def __init__(
        self,
        persist_directory: str,
        db_name: str = "default",
        map_size: int = None,
        writemap: bool = False,
    ):
        """
        初始化 KV 数据库

        Args:
            persist_directory: 持久化根目录
            db_name: 数据库名称（用于区分不同 KV 库）
            map_size: LMDB 映射文件大小（字节），默认 100MB
            writemap: 是否使用写入映射（更快但兼容性差）
        """
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.db_name = db_name
        self.db_path = self.persist_directory / f"{db_name}.lmdb"

        self._map_size = map_size or self.DEFAULT_MAP_SIZE
        self._writemap = writemap

        # 线程锁：LMDB 只支持单线程写，需要通过锁控制
        self._write_lock = threading.Lock()

        # 打开 LMDB 环境
        # max_dbs=1 因为我们在一个 env 里存所有 kv，用前缀区分
        self._env = lmdb.open(
            str(self.db_path),
            map_size=self._map_size,
            writemap=writemap,
            subdir=True,  # db_path 是目录
            metasync=False,  # 关闭元数据同步，提升性能
            sync=False,  # 关闭同步，更快但断电可能丢数据（kv 场景可接受）
        )

    def _encode_key(self, key: str) -> bytes:
        """将字符串 key 编码为 bytes"""
        return key.encode("utf-8")

    def _decode_key(self, key: bytes) -> str:
        """将 bytes key 解码为字符串"""
        return key.decode("utf-8")

    def _encode_value(self, value: Any) -> bytes:
        """将值序列化为 bytes（JSON + expire_at）"""
        # 存储格式：{"data": <json数据>, "expire_at": <时间戳或null>}
        container = {
            "data": value,
            "expire_at": None,  # TTL 由外部 expire() 设置
        }
        return json.dumps(container, ensure_ascii=False, default=str).encode("utf-8")

    def _decode_value(self, data: bytes) -> tuple:
        """从 bytes 反序列化值，返回 (data, expire_at)"""
        container = json.loads(data.decode("utf-8"))
        return container["data"], container.get("expire_at")

    def _is_expired(self, expire_at: Optional[float]) -> bool:
        """检查是否过期"""
        if expire_at is None:
            return False
        return time.time() > expire_at

    def _get_with_check(self, txn: lmdb.Transaction, key: str) -> Any:
        """获取值并检查过期，返回 (value, is_expired_or_missing)"""
        k = self._encode_key(key)
        raw = txn.get(k)
        if raw is None:
            return None, True

        value, expire_at = self._decode_value(raw)
        if self._is_expired(expire_at):
            return None, True
        return value, False

    # ---- 基础操作 ----

    def set(self, key: str, value: Any) -> bool:
        """
        设置键值对

        Args:
            key: 键名（字符串）
            value: 值（必须是 JSON 可序列化对象）

        Returns:
            是否成功
        """
        with self._write_lock:
            with self._env.begin(write=True) as txn:
                k = self._encode_key(key)

                # 尝试获取现有值，保留原来的 expire_at
                old_raw = txn.get(k)
                expire_at = None
                if old_raw:
                    try:
                        _, old_expire = self._decode_value(old_raw)
                        expire_at = old_expire
                    except Exception:
                        pass

                # 构建新值
                container = {"data": value, "expire_at": expire_at}
                v = json.dumps(container, ensure_ascii=False, default=str).encode("utf-8")

                txn.put(k, v)
        return True

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取值

        Args:
            key: 键名
            default: 默认值（key 不存在或已过期时返回）

        Returns:
            存储的值或默认值
        """
        with self._env.begin(write=False) as txn:
            value, missing = self._get_with_check(txn, key)

        # 如果过期，在写锁下删除
        if missing:
            # 再次检查，可能是刚过期
            with self._env.begin(write=True) as txn:
                k = self._encode_key(key)
                raw = txn.get(k)
                if raw:
                    try:
                        _, expire_at = self._decode_value(raw)
                        if self._is_expired(expire_at):
                            txn.delete(k)
                    except Exception:
                        pass
            return default

        return value

    def delete(self, key: str) -> bool:
        """
        删除键值对

        Args:
            key: 键名

        Returns:
            是否成功删除
        """
        with self._write_lock:
            with self._env.begin(write=True) as txn:
                k = self._encode_key(key)
                result = txn.delete(k)
        return result

    def exists(self, key: str) -> bool:
        """检查 key 是否存在且未过期"""
        with self._env.begin(write=False) as txn:
            value, missing = self._get_with_check(txn, key)
            if missing:
                return False
            return True

    def keys(self, pattern: str = "*") -> List[str]:
        """
        返回所有匹配的键（未过期）

        Args:
            pattern: Glob 模式，如 "mem_*", "user:*"
        """
        # 转换 Glob 模式
        # 由于 LMDB 键是有序存储，我们遍历时匹配
        prefix = pattern.split("*")[0] if "*" in pattern else pattern
        prefix_bytes = self._encode_key(prefix) if prefix else None

        results = []
        now = time.time()

        with self._env.begin(write=False) as txn:
            cursor = txn.cursor()
            for key_bytes, value_bytes in cursor:
                key = self._decode_key(key_bytes)

                # 前缀匹配
                if prefix_bytes and not key_bytes.startswith(prefix_bytes):
                    continue

                # Glob 匹配（简化：只处理尾部的 *）
                if "*" in pattern:
                    parts = pattern.split("*")
                    if len(parts) == 2:  # 如 "mem_*"
                        if not key.startswith(parts[0]) or not key.endswith(parts[1]):
                            continue
                    elif len(parts) == 3 and parts[0] == "":  # "*suffix"
                        if not key.endswith(parts[2]):
                            continue

                # 检查过期
                try:
                    _, expire_at = self._decode_value(value_bytes)
                    if self._is_expired(expire_at):
                        continue
                except Exception:
                    pass

                results.append(key)

        return results

    def clear(self) -> int:
        """清空所有数据，返回删除的键数量"""
        with self._write_lock:
            with self._env.begin(write=True) as txn:
                count = 0
                cursor = txn.cursor()
                for key_bytes, _ in cursor:
                    txn.delete(key_bytes)
                    count += 1
        return count

    # ---- 批量操作 ----

    def mset(self, items: Dict[str, Any]) -> bool:
        """批量设置"""
        if not items:
            return True

        with self._write_lock:
            with self._env.begin(write=True) as txn:
                for key, value in items.items():
                    k = self._encode_key(key)

                    # 保留原来的 expire_at
                    old_raw = txn.get(k)
                    expire_at = None
                    if old_raw:
                        try:
                            _, old_expire = self._decode_value(old_raw)
                            expire_at = old_expire
                        except Exception:
                            pass

                    container = {"data": value, "expire_at": expire_at}
                    v = json.dumps(container, ensure_ascii=False, default=str).encode("utf-8")
                    txn.put(k, v)
        return True

    def mget(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取，返回存在的键值对（不包含过期的）"""
        if not keys:
            return {}

        result = {}
        now = time.time()
        expired_keys = []

        with self._env.begin(write=False) as txn:
            for key in keys:
                value, missing = self._get_with_check(txn, key)
                if not missing:
                    result[key] = value

        # 异步清理过期键（在下次写入时顺便清理也可）
        # 这里不做主动删除，因为 mget 可能频繁调用

        return result

    def mdelete(self, keys: List[str]) -> int:
        """批量删除，返回删除数量"""
        if not keys:
            return 0

        count = 0
        with self._write_lock:
            with self._env.begin(write=True) as txn:
                for key in keys:
                    k = self._encode_key(key)
                    if txn.delete(k):
                        count += 1
        return count

    # ---- 特殊操作 ----

    def expire(self, key: str, ttl_seconds: int) -> bool:
        """
        设置过期时间（TTL）

        Args:
            key: 键名
            ttl_seconds: 过期秒数（<=0 表示移除过期时间）

        Returns:
            是否成功
        """
        if ttl_seconds <= 0:
            # 移除过期时间
            return self._set_expire_at(key, None)

        expire_at = time.time() + ttl_seconds

        with self._write_lock:
            with self._env.begin(write=True) as txn:
                k = self._encode_key(key)
                raw = txn.get(k)
                if raw is None:
                    return False

                try:
                    value, _ = self._decode_value(raw)
                except Exception:
                    return False

                container = {"data": value, "expire_at": expire_at}
                v = json.dumps(container, ensure_ascii=False, default=str).encode("utf-8")
                txn.put(k, v)
        return True

    def _set_expire_at(self, key: str, expire_at: Optional[float]) -> bool:
        """内部方法：设置过期时间戳"""
        with self._write_lock:
            with self._env.begin(write=True) as txn:
                k = self._encode_key(key)
                raw = txn.get(k)
                if raw is None:
                    return False

                try:
                    value, _ = self._decode_value(raw)
                except Exception:
                    return False

                container = {"data": value, "expire_at": expire_at}
                v = json.dumps(container, ensure_ascii=False, default=str).encode("utf-8")
                txn.put(k, v)
        return True

    def ttl(self, key: str) -> int:
        """
        获取剩余生存时间

        Returns:
            剩余秒数，-1 表示永不过期，-2 表示不存在
        """
        with self._env.begin(write=False) as txn:
            raw = txn.get(self._encode_key(key))
            if raw is None:
                return -2

            try:
                _, expire_at = self._decode_value(raw)
            except Exception:
                return -2

            if expire_at is None:
                return -1

            remaining = expire_at - time.time()
            return int(remaining) if remaining > 0 else -2

    # ---- 迭代器 ----

    def scan(self, pattern: str = "*", batch_size: int = 100) -> Iterator:
        """
        游标迭代遍历所有键值对（未过期）

        Args:
            pattern: Glob 模式
            batch_size: 已废弃，LMDB 游标天然连续

        Yields:
            (key, value) 元组
        """
        prefix = pattern.split("*")[0] if "*" in pattern else pattern
        prefix_bytes = self._encode_key(prefix) if prefix else None

        with self._env.begin(write=False) as txn:
            cursor = txn.cursor()

            # 跳到前缀位置
            if prefix_bytes:
                cursor.set_range(prefix_bytes)

            while True:
                item = cursor.item()
                if item is None:
                    break
                key_bytes, value_bytes = item

                # 前缀过滤
                if prefix_bytes and not key_bytes.startswith(prefix_bytes):
                    break

                # 检查过期
                try:
                    value, expire_at = self._decode_value(value_bytes)
                    if self._is_expired(expire_at):
                        if not cursor.next():
                            break
                        continue
                except Exception:
                    if not cursor.next():
                        break
                    continue

                key = self._decode_key(key_bytes)

                # Glob 匹配（简化）
                if "*" in pattern:
                    parts = pattern.split("*")
                    if len(parts) == 2:
                        if not key.startswith(parts[0]) or not key.endswith(parts[1]):
                            if not cursor.next():
                                break
                            continue

                yield (key, value)

                if not cursor.next():
                    break

    # ---- 工具方法 ----

    def stat(self) -> dict:
        """返回 LMDB 状态信息"""
        with self._env.begin(write=False) as txn:
            stat = txn.stat()
            return {
                "entries": stat["entries"],
                "branch_pages": stat["branch_pages"],
                "leaf_pages": stat["leaf_pages"],
                "overflow_pages": stat["overflow_pages"],
                "map_size": self._env.info()["map_size"],
            }

    # ---- 上下文管理 ----

    def close(self):
        """关闭数据库连接"""
        if self._env:
            self._env.close()
            self._env = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __contains__(self, key: str) -> bool:
        """支持 `in` 操作符"""
        return self.exists(key)

    def __len__(self) -> int:
        """返回键数量（未过期）"""
        count = 0
        now = time.time()
        with self._env.begin(write=False) as txn:
            cursor = txn.cursor()
            for key_bytes, value_bytes in cursor:
                try:
                    _, expire_at = self._decode_value(value_bytes)
                    if not self._is_expired(expire_at):
                        count += 1
                except Exception:
                    count += 1  # 旧格式数据也算
        return count
