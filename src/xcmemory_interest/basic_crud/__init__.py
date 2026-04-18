"""
星尘记忆 - 向量数据库 CRUD 模块

提供记忆的完整 CRUD 操作接口：
- VecDBCRUD: 每个槽位独立 Chroma Collection（64维），支持精准子空间查找
- BasicCRUD: 旧版（单 Collection 384 维），保留兼容
"""

from .vec_db_crud import VecDBCRUD, Memory, SearchResult, EmbeddingMode
from .basic_crud import BasicCRUD

__all__ = ["VecDBCRUD", "BasicCRUD", "Memory", "SearchResult", "EmbeddingMode"]
