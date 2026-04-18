"""
星尘记忆 - 向量数据库模块
Vector Database Module for StarDust Memory

数据分离设计：
- Chroma: 只存储 memory_id 和向量，用于快速检索
- basic_crud: 存储完整的 Memory 对象（包括 content）

使用示例：
```python
from models.xcmemory_interest.vector_db import ChromaVectorDB, SubspaceSearcher, HybridSearcher

# 创建向量数据库
db = ChromaVectorDB(persist_directory="./data/vector_db")

# 添加向量
memory_id = db.add(vector=np.array([...]), metadata={"subject": "我"})

# 搜索
results = db.search(query_vector=np.array([...]), top_k=5)

# 子空间搜索
searcher = SubspaceSearcher(db)
results = searcher.search(
    query_slots={"subject": "我"},
    query_slot_vectors={"subject": np.array([...])},
    top_k=5,
)

# 混合搜索
searcher = HybridSearcher(db)
results = searcher.search(
    query_vector=np.array([...]),
    mode="hybrid",
    keyword_results=["mem_001"],
    graph_results=["mem_002"],
)
```
"""

from .chroma_vector_db import ChromaVectorDB, SubspaceSearcher, HybridSearcher
from .reranker import (
    SubspaceReranker,
    ResultConditioningReranker,
    DynamicReranker,
    ProbabilitySampler,
    DistanceAwareSampler,
)

__all__ = [
    "ChromaVectorDB",
    "SubspaceSearcher",
    "HybridSearcher",
    "SubspaceReranker",
    "ResultConditioningReranker",
    "DynamicReranker",
    "ProbabilitySampler",
    "DistanceAwareSampler",
]
