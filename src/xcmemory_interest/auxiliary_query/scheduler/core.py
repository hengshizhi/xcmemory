"""
Scheduler - 数据库调度器
"""

from typing import Any, Dict, List, Optional

from ..storage.kv_db import KVDatabase
from ..storage.sql_db import SQLDatabase


class Scheduler:
    """
    数据库调度器

    管理 KV 数据库和 SQL 数据库的生命周期。

    目录结构：
        base_directory/
        ├── kv/              # KV 数据库
        │   └── {db_name}.sqlite3
        └── sql/             # SQL 数据库
            └── {db_name}.sqlite3
    """

    def __init__(self, base_directory: str = "./data/aux_db"):
        """
        初始化调度器

        Args:
            base_directory: 持久化根目录
        """
        self.base_directory = base_directory
        self._kv_instances: Dict[str, KVDatabase] = {}
        self._sql_instances: Dict[str, SQLDatabase] = {}

        # 确保目录存在
        import os
        os.makedirs(f"{base_directory}/kv", exist_ok=True)
        os.makedirs(f"{base_directory}/sql", exist_ok=True)

    # ---- KV 数据库管理 ----

    def create_kv(self, db_name: str) -> KVDatabase:
        """
        创建或获取 KV 数据库

        Args:
            db_name: 数据库名称（如 "cache"，会存储为 "kv_cache.lmdb"）

        Returns:
            KVDatabase 实例
        """
        if db_name in self._kv_instances:
            return self._kv_instances[db_name]

        # 添加 kv_ 前缀，内部存储为 kv_{db_name}.lmdb
        storage_name = f"kv_{db_name}"
        kv = KVDatabase(
            persist_directory=f"{self.base_directory}/kv",
            db_name=storage_name,
        )
        self._kv_instances[db_name] = kv
        return kv

    def get_kv(self, db_name: str) -> Optional[KVDatabase]:
        """
        获取已创建的 KV 数据库

        Args:
            db_name: 数据库名称

        Returns:
            KVDatabase 实例，不存在返回 None
        """
        return self._kv_instances.get(db_name)

    def delete_kv(self, db_name: str) -> bool:
        """
        删除 KV 数据库

        Args:
            db_name: 数据库名称

        Returns:
            是否成功
        """
        if db_name in self._kv_instances:
            self._kv_instances[db_name].close()
            del self._kv_instances[db_name]

        import os
        import shutil
        path = f"{self.base_directory}/kv/kv_{db_name}.lmdb"
        if os.path.exists(path):
            shutil.rmtree(path)
            return True
        return False

    def kv_exists(self, db_name: str) -> bool:
        """
        检查 KV 数据库是否存在

        Args:
            db_name: 数据库名称

        Returns:
            是否存在
        """
        if db_name in self._kv_instances:
            return True
        import os
        return os.path.exists(f"{self.base_directory}/kv/kv_{db_name}.lmdb")

    def list_kv(self) -> List[str]:
        """
        列出所有 KV 数据库

        Returns:
            数据库名称列表（原始名称，不带 kv_ 前缀）
        """
        import os
        kv_dir = f"{self.base_directory}/kv"
        if not os.path.exists(kv_dir):
            return []
        result = []
        for f in os.listdir(kv_dir):
            if f.startswith("kv_") and f.endswith(".lmdb") and os.path.isdir(os.path.join(kv_dir, f)):
                # 去掉前缀 "kv_" 和后缀 ".lmdb"
                db_name = f[3:-5]
                result.append(db_name)
        return result

    # ---- SQL 数据库管理 ----

    def create_sql(self, db_name: str) -> SQLDatabase:
        """
        创建或获取 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            SQLDatabase 实例
        """
        if db_name in self._sql_instances:
            return self._sql_instances[db_name]

        sql = SQLDatabase(
            persist_directory=f"{self.base_directory}/sql",
            db_name=db_name,
        )
        self._sql_instances[db_name] = sql
        return sql

    def get_sql(self, db_name: str) -> Optional[SQLDatabase]:
        """
        获取已创建的 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            SQLDatabase 实例，不存在返回 None
        """
        return self._sql_instances.get(db_name)

    def delete_sql(self, db_name: str) -> bool:
        """
        删除 SQL 数据库

        Args:
            db_name: 数据库名称

        Returns:
            是否成功
        """
        if db_name in self._sql_instances:
            self._sql_instances[db_name].close()
            del self._sql_instances[db_name]

        import os
        path = f"{self.base_directory}/sql/sql_{db_name}.sqlite3"
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def sql_exists(self, db_name: str) -> bool:
        """
        检查 SQL 数据库是否存在

        Args:
            db_name: 数据库名称

        Returns:
            是否存在
        """
        if db_name in self._sql_instances:
            return True
        import os
        return os.path.exists(f"{self.base_directory}/sql/sql_{db_name}.sqlite3")

    def list_sql(self) -> List[str]:
        """
        列出所有 SQL 数据库

        Returns:
            数据库名称列表
        """
        import os
        sql_dir = f"{self.base_directory}/sql"
        if not os.path.exists(sql_dir):
            return []
        return [
            f[:-8]  # 去掉 "_sql.db" 后缀
            for f in os.listdir(sql_dir)
            if f.startswith("sql_") and f.endswith(".sqlite3")
        ]

    # ---- 批量操作 ----

    def create_all(self, kv_names: List[str] = None, sql_names: List[str] = None):
        """
        批量创建数据库

        Args:
            kv_names: KV 数据库名称列表
            sql_names: SQL 数据库名称列表
        """
        if kv_names:
            for name in kv_names:
                self.create_kv(name)
        if sql_names:
            for name in sql_names:
                self.create_sql(name)

    def delete_all(self):
        """删除所有数据库"""
        for name in list(self._kv_instances.keys()):
            self.delete_kv(name)
        for name in list(self._sql_instances.keys()):
            self.delete_sql(name)

    def list_all(self) -> Dict[str, List[str]]:
        """
        列出所有数据库

        Returns:
            {"kv": [...], "sql": [...]}
        """
        return {
            "kv": self.list_kv(),
            "sql": self.list_sql(),
        }

    # ---- 生命周期 ----

    def close(self):
        """关闭所有数据库连接"""
        for kv in self._kv_instances.values():
            kv.close()
        for sql in self._sql_instances.values():
            sql.close()
        self._kv_instances.clear()
        self._sql_instances.clear()

    def close_kv(self, db_name: str):
        """
        关闭指定 KV 数据库

        Args:
            db_name: 数据库名称
        """
        if db_name in self._kv_instances:
            self._kv_instances[db_name].close()
            del self._kv_instances[db_name]

    def close_sql(self, db_name: str):
        """
        关闭指定 SQL 数据库

        Args:
            db_name: 数据库名称
        """
        if db_name in self._sql_instances:
            self._sql_instances[db_name].close()
            del self._sql_instances[db_name]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self) -> str:
        return f"Scheduler(kv={list(self._kv_instances.keys())}, sql={list(self._sql_instances.keys())})"
