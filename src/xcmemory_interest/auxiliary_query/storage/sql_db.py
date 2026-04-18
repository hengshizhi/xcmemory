"""
SQLDatabase - 基于 SQLite 的 SQL 数据库接口
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


class SQLDatabase:
    """
    SQL 数据库接口

    基于 SQLite 实现，提供完整的 SQL 执行能力。

    目录结构：
        persist_directory/
        └── sql_{db_name}.sqlite3
    """

    def __init__(self, persist_directory: str, db_name: str = "default"):
        """
        初始化 SQL 数据库

        Args:
            persist_directory: 持久化根目录
            db_name: 数据库名称
        """
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.db_name = db_name
        self.db_path = self.persist_directory / f"sql_{db_name}.sqlite3"

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._in_transaction = False  # 事务状态标志

    def _auto_commit(self):
        """自动提交（非事务模式下自动提交）"""
        if not self._in_transaction:
            self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将 Row 对象转换为字典"""
        return dict(row)

    # ---- DDL 操作 ----

    def create_table(
        self,
        table_name: str,
        columns: Dict[str, str],
        if_not_exists: bool = True,
    ) -> bool:
        """
        创建表

        Args:
            table_name: 表名
            columns: 列定义，如 {"id": "TEXT PRIMARY KEY", "name": "TEXT NOT NULL"}
            if_not_exists: 是否添加 IF NOT EXISTS

        Returns:
            是否成功
        """
        col_defs = ", ".join([f"{name} {dtype}" for name, dtype in columns.items()])
        sql = f"CREATE TABLE {'IF NOT EXISTS' if if_not_exists else ''} {table_name} ({col_defs})"
        cur = self._conn.cursor()
        cur.execute(sql)
        self._auto_commit()
        return True

    def drop_table(self, table_name: str, if_exists: bool = True) -> bool:
        """删除表"""
        sql = f"DROP TABLE {'IF EXISTS' if if_exists else ''} {table_name}"
        cur = self._conn.cursor()
        cur.execute(sql)
        self._auto_commit()
        return True

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cur.fetchone() is not None

    # ---- DML 操作 ----

    def insert(
        self,
        table_name: str,
        data: Dict[str, Any],
        or_replace: bool = False,
    ) -> bool:
        """
        插入数据

        Args:
            table_name: 表名
            data: 列名到值的映射
            or_replace: 是否使用 OR REPLACE

        Returns:
            是否成功
        """
        columns = list(data.keys())
        placeholders = ",".join(["?" for _ in columns])
        col_names = ",".join(columns)
        prefix = "INSERT OR REPLACE INTO" if or_replace else "INSERT INTO"
        sql = f"{prefix} {table_name} ({col_names}) VALUES ({placeholders})"
        cur = self._conn.cursor()
        cur.execute(sql, tuple(data.values()))
        self._auto_commit()
        return True

    def insert_many(
        self,
        table_name: str,
        data_list: List[Dict[str, Any]],
    ) -> int:
        """
        批量插入

        Args:
            table_name: 表名
            data_list: 数据列表

        Returns:
            插入的行数
        """
        if not data_list:
            return 0
        columns = list(data_list[0].keys())
        placeholders = ",".join(["?" for _ in columns])
        col_names = ",".join(columns)
        sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
        cur = self._conn.cursor()
        # executemany 返回的 rowcount 不准确，需要单独统计
        total = 0
        for data in data_list:
            cur.execute(sql, tuple(data.values()))
            total += cur.rowcount
        self._auto_commit()
        return total

    def update(
        self,
        table_name: str,
        data: Dict[str, Any],
        where: Dict[str, Any],
    ) -> int:
        """
        更新数据

        Args:
            table_name: 表名
            data: 要更新的列和值
            where: WHERE 条件（AND 连接）

        Returns:
            影响的行数
        """
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        where_clause = " AND ".join([f"{k} = ?" for k in where.keys()])
        sql = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
        params = list(data.values()) + list(where.values())
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._auto_commit()
        return cur.rowcount

    def delete(
        self,
        table_name: str,
        where: Dict[str, Any],
    ) -> int:
        """
        删除数据

        Args:
            table_name: 表名
            where: WHERE 条件

        Returns:
            影响的行数
        """
        where_clause = " AND ".join([f"{k} = ?" for k in where.keys()])
        sql = f"DELETE FROM {table_name} WHERE {where_clause}"
        cur = self._conn.cursor()
        cur.execute(sql, tuple(where.values()))
        self._auto_commit()
        return cur.rowcount

    # ---- 查询操作 ----

    def select(
        self,
        table_name: str,
        columns: List[str] = None,
        where: Dict[str, Any] = None,
        order_by: str = None,
        order: str = "ASC",
        limit: int = None,
        offset: int = None,
    ) -> List[Dict[str, Any]]:
        """
        查询数据

        Args:
            table_name: 表名
            columns: 要查询的列（None 表示所有）
            where: WHERE 条件
            order_by: 排序列名
            order: ASC 或 DESC
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            查询结果列表
        """
        col_clause = ", ".join(columns) if columns else "*"
        sql = f"SELECT {col_clause} FROM {table_name}"
        params = []

        if where:
            where_clause = " AND ".join([f"{k} = ?" for k in where.keys()])
            sql += f" WHERE {where_clause}"
            params = list(where.values())

        if order_by:
            order = order.upper()
            if order not in ("ASC", "DESC"):
                order = "ASC"
            sql += f" ORDER BY {order_by} {order}"

        if limit is not None:
            sql += f" LIMIT {limit}"
            if offset is not None:
                sql += f" OFFSET {offset}"

        cur = self._conn.cursor()
        cur.execute(sql, params)
        return [self._row_to_dict(row) for row in cur.fetchall()]

    def query(self, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """
        执行原生 SQL 查询

        Args:
            sql: SQL 语句（应使用 ? 占位符）
            params: 查询参数

        Returns:
            查询结果列表
        """
        cur = self._conn.cursor()
        cur.execute(sql, params)
        # 判断是否是 SELECT 语句
        if sql.strip().upper().startswith("SELECT"):
            return [self._row_to_dict(row) for row in cur.fetchall()]
        return []

    def execute(self, sql: str, params: Tuple = ()) -> bool:
        """
        执行原生 SQL（用于 INSERT/UPDATE/DELETE）

        Args:
            sql: SQL 语句
            params: 参数

        Returns:
            是否成功
        """
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._auto_commit()
        return True

    # ---- 事务支持 ----

    def begin(self):
        """开启事务"""
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True

    def commit(self):
        """提交事务"""
        self._conn.commit()
        self._in_transaction = False

    def rollback(self):
        """回滚事务"""
        self._conn.rollback()
        self._in_transaction = False

    @contextmanager
    def transaction(self):
        """
        上下文管理器，自动提交/回滚

        Usage:
            with db.transaction():
                db.insert(...)
                db.update(...)
        """
        try:
            self.begin()
            yield self
            self.commit()
        except Exception:
            self.rollback()
            raise

    # ---- 工具方法 ----

    def count(self, table_name: str, where: Dict[str, Any] = None) -> int:
        """返回行数"""
        sql = f"SELECT COUNT(*) FROM {table_name}"
        params = []
        if where:
            where_clause = " AND ".join([f"{k} = ?" for k in where.keys()])
            sql += f" WHERE {where_clause}"
            params = list(where.values())
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def clear(self, table_name: str = None):
        """
        清空数据

        Args:
            table_name: 表名（None 表示清空所有表）
        """
        if table_name:
            cur = self._conn.cursor()
            cur.execute(f"DELETE FROM {table_name}")
            self._auto_commit()
        else:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for t in tables:
                cur.execute(f"DELETE FROM {t}")
            self._auto_commit()

    def list_tables(self) -> List[str]:
        """列出所有表"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row[0] for row in cur.fetchall()]

    def get_columns(self, table_name: str) -> List[Dict[str, str]]:
        """获取表结构"""
        cur = self._conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name})")
        return [{"name": row[1], "type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]} for row in cur.fetchall()]

    # ---- 上下文管理 ----

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
