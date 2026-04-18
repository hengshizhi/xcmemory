# 基础增删查改模块

> 管理人：TODO
> 状态：已完成实现

## 职责

提供记忆的基础 CRUD 操作接口，管理记忆的存储和检索。

## 架构设计

### 数据分离

```
┌─────────────────────────────────────────────────────────────────┐
│                        BasicCRUD                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  write(query_sentence, content) ──→ Memory{content,向量}      │
│                                          │                      │
│                    ┌─────────────────────┼─────────────────────┤
│                    │                     │                     │
│                    ▼                     ▼                     │
│            KV数据库(SQLite)        向量数据库(Chroma)           │
│            Memory对象              memory_id → vector[384]     │
│            content, lifecycle      metadata{query_sentence}    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 两种嵌入模式

| 模式 | 说明 | 用途 |
|------|------|------|
| INTEREST | 经过 InterestEncoder 自注意力处理 | 语义相似度检索 |
| RAW | 不过自注意力，直接拼接 | 槽位匹配、辅助查询 |

### 写入流程

```
write(query_sentence, content, lifecycle)
  │
  ├─→ _parse_query_sentence() → QuerySlots
  │
  ├─→ pipeline.encode(slots, use_raw=False) → interest_vec [384]
  │
  ├─→ pipeline.encode(slots, use_raw=True) → raw_vec [384]
  │
  ├─→ Memory 对象 ──→ KV数据库(SQLite)
  │
  └─→ vector_db.add(memory_id, vec) ──→ 向量数据库(Chroma)
```

### 查询流程

```
search(query_slots, embedding_mode=INTEREST)
  │
  ├─→ _parse_query_slots() → QuerySlots
  │
  ├─→ pipeline.encode(slots, use_raw=False) → query_vec [384]
  │
  ├─→ vector_db.search(query_vec) → [memory_id, ...]
  │
  └─→ for memory_id: read(memory_id) → Memory
```

## API 设计

```python
from models.xcmemory_interest.basic_crud import BasicCRUD, EmbeddingMode

# === 初始化 ===
crud = BasicCRUD(
    persist_directory="./data/xcmemory_kv",  # KV数据库目录
    vector_db_path="./data/vector_db",        # 向量数据库目录
)

# === 写入 ===
memory_id = crud.write(
    query_sentence="<平时><我><学><编程><喜欢><有收获>",
    content="我平时喜欢学编程，学了很有收获",
    lifecycle=30,
    embedding_mode=EmbeddingMode.INTEREST,  # 或 RAW
)

# === 读取 ===
memory = crud.read(memory_id)
print(memory.content, memory.query_sentence)

# === 更新 ===
crud.update(memory_id, content="新内容", lifecycle=10)

# === 删除 ===
crud.delete(memory_id)

# === 搜索（全空间） ===
results = crud.search_fullspace(
    query_slots={"subject": "我", "action": "学"},
    top_k=5,
    embedding_mode=EmbeddingMode.INTEREST,
)

# === 搜索（子空间） ===
results = crud.search_subspace(
    query_slots={"subject": "我", "action": "学"},
    top_k=5,
    embedding_mode=EmbeddingMode.INTEREST,
    rerank=True,
)

# === 工具方法 ===
count = crud.count()
exists = crud.exists(memory_id)
crud.clear()  # 清空所有记忆

# === 关闭连接 ===
crud.close()
```

## 数据模型

### EmbeddingMode 枚举

```python
class EmbeddingMode(Enum):
    INTEREST = "interest"  # 兴趣嵌入：经过自注意力，语义理解强
    RAW = "raw"          # 原始嵌入：直接拼接，计算快
```

### Memory

```python
@dataclass
class Memory:
    id: str                          # 记忆唯一ID
    query_sentence: str               # 查询句，如 "<平时><我><学><编程><喜欢><有收获>"
    content: str                     # 记忆内容
    lifecycle: int                    # 生命周期
    created_at: datetime
    updated_at: datetime
```

### SearchResult

```python
@dataclass
class SearchResult:
    memory_id: str
    distance: float
    score: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)
    memory: Optional[Memory] = None  # 完整的 Memory 对象
    sample_prob: float = 0.0        # 采样概率（用于概率采样器）
```

## 嵌入模式对比

| 特性 | INTEREST (兴趣嵌入) | RAW (原始嵌入) |
|------|-------------------|---------------|
| 处理方式 | 经过自注意力 | 直接拼接 |
| 语义理解 | 强（学习槽位间依赖） | 弱（独立槽位） |
| 计算成本 | 较高 | 较低 |
| 适用场景 | 语义相似度检索 | 精确槽位匹配 |
| 槽位相关性 | 自动学习 | 需要手动设计 |

## 与其他模块的关系

```
embedding_coder/
├── InterestEncoder      # 向量生成模型
└── QueryEncoderPipeline # 查询编码管道

vector_db/
├── ChromaVectorDB       # 向量存储
├── SubspaceSearcher     # 子空间搜索
└── reranker.py         # 重排序

basic_crud/             # 本模块
├── 整合 embedding_coder 生成向量
├── 整合 vector_db 进行向量搜索
└── 管理 KV数据库(SQLite) 存储 Memory 对象
```

## 待实现

- [ ] 元数据索引设计（更复杂的查询条件）
- [ ] 批量写入优化
- [ ] 事务支持
- [ ] 生命周期自动衰减
