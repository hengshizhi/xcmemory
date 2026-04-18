# 向量数据库模块

> 管理人：TODO
> 状态：已完成实现

## 架构设计

### 数据分离原则

```
┌─────────────────────────────────────────────────────────────────┐
│                        检索流程                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. vector_db.search(query_vector) → [memory_id, ...]           │
│                         │                                       │
│                         ▼                                       │
│  2. basic_crud.read(memory_id) → Memory{content, ...}           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Chroma (vector_db):
├── memory_id → vector [384]
└── metadata: {slot_name: slot_value}  # 只存字符串，用于子空间过滤

basic_crud:
└── memory_id → Memory {query_sentence, content, lifecycle, ...}
```

### 为什么分离

1. **性能**：Chroma 只存向量，体积小，检索快
2. **一致性**：向量和内容分开管理，避免重复存储
3. **灵活性**：可以独立更新内容或重新计算向量

## API 设计

```python
# === ChromaVectorDB: 向量存储 ===

db = ChromaVectorDB(persist_directory="./data/vector_db")

# 添加（只存向量+元数据，不存 content）
memory_id = db.add(
    vector=np.array([...]),           # [384] 记忆向量
    metadata={"subject": "我", "action": "学"},  # 槽位信息
)

# 搜索（全空间）
results = db.search(query_vector=np.array([...]), top_k=5)
# → [{"memory_id": "mem_xxx", "distance": 0.12, "metadata": {...}}]

# 获取
item = db.get(memory_id, include_vector=True)

# 更新/删除
db.update(memory_id, vector=np.array([...]), metadata={...})
db.delete(memory_id)


# === SubspaceSearcher: 子空间搜索 ===

searcher = SubspaceSearcher(db)
results = searcher.search(
    query_vector=np.array([...]),           # [384] 完整查询向量
    query_slot_vectors={"subject": np.array([...])},  # 槽位向量
    top_k=5,
    rerank=True,
)


# === HybridSearcher: 混合搜索 ===

searcher = HybridSearcher(db)
results = searcher.search(
    query_vector=np.array([...]),           # [384] 查询向量
    query_context={"result_focused": True},  # 上下文
    top_k=5,
    mode="hybrid",                          # hybrid | vector | dynamic
    keyword_results=["mem_001", "mem_002"],  # 关键字搜索结果
    graph_results=["mem_003"],               # 图搜索结果
)


# === ProbabilitySampler: 概率采样 ===

sampler = ProbabilitySampler(random_seed=42)  # 可选 sigma
sampled = sampler.sample(
    candidates=results,                      # Chroma 返回的候选
    query_vector=np.array([...]),           # [384] 查询向量
    top_k=100,                              # Chroma 返回的候选数
    n_select=10,                            # 最终选择数 N(top_k)
)
# → [{memory_id, distance, sample_prob}, ...]
```

## 实现选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 向量后端 | Chroma | 支持元数据过滤，SQLite 持久化 |
| 索引类型 | HNSW | Chroma 内置，检索性能好 |
| 持久化 | SQLite/Chroma | 轻量，单文件 |

---

## 概率采样器设计

### 原理

```
1. top_k(n): 用较大 n 检索候选集
2. 定义 a = N(n) / n, a < 1 (归一化因子)
3. 对于距离 L，定义 x=0 时概率最大的正态分布 f(X=L)
4. 选中概率 P(L) = a * f(L) / Z

正态分布概率密度函数：
    f(L) = (1 / (σ * sqrt(2π))) * exp(-(L - μ)² / (2σ²))
其中 μ = 0（x=0 时概率最大），σ 是标准差
```

### Sigma 自适应

- **默认**: sigma = mean(distances) * 0.5
- 距离近的候选被选中概率高
- 距离远的候选被选中概率低
- sigma 可手动指定，控制分布尖锐程度

### 算法流程

```python
# 1. 计算归一化因子 a = N(n) / n
a = n_select / top_k

# 2. 计算每个候选的距离 L 和概率 f(L)
for cand in candidates:
    dist = ||query_vector - cand.vector||  # L2 距离
    prob_raw = f(dist)  # 正态分布概率密度

# 3. P(L) = a * f(L)，归一化使 sum = n_select
scaled_probs = a * raw_probs
norm_probs = scaled_probs / scaled_probs.sum() * n_select

# 4. 多项式采样
selected_indices = random.choice(n, size=n_select, p=norm_probs/norm_probs.sum())

# 5. 去重（保留首次出现的顺序）
```

### 与确定性采样的对比

| 特性 | ProbabilitySampler | DistanceAwareSampler |
|------|-------------------|---------------------|
| 随机性 | 有（可重现） | 无 |
| 选中数量 | 期望 = n_select | 最多 n_select |
| 适用场景 | 需要多样性的采样 | 需要确定性的截断 |
| 概率分布 | 距离近的概率高 | 距离 < threshold 直接入选 |

## 待优化

- [ ] 批量写入优化
- [ ] 索引类型选择（HNSW / IVF / Flat）
- [ ] 分布式支持
