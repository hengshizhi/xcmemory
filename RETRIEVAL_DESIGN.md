# 星尘记忆系统 - 检索设计构想

> 本文档描述星尘记忆系统的检索（查询）部分设计。写入部分不在本文范围内。

---

## 一、概述

星尘记忆是一个基于**结构化向量检索**的个性化记忆系统。其核心思想是：

1. **结构化记忆**：将记忆分解为 6 个语义槽位
2. **隐式图结构**：记忆之间通过槽位共享形成隐式图
3. **多模检索**：支持子空间、全空间、混合三种检索模式

---

## 二、记忆格式

### 2.1 槽位定义

每条记忆由 6 个槽位组成：

```
<时间词><主体><动作><宾语><目的><结果>
```

| 槽位 | 名称 | 说明 | 示例 |
|------|------|------|------|
| time | 时间词 | 动作发生的时间背景 | 平时、经常、偶尔 |
| subject | 主体 | 执行动作的实体 | 我、他、我们 |
| action | 动作 | 主要行为 | 做、玩、学、看 |
| object | 宾语 | 动作的客体 | 实验、游戏、编程 |
| purpose | 目的 | 动作的目的/原因 | 喜欢、为了、学习进步 |
| result | 结果 | 动作产生的结果 | 成功了、有收获 |

### 2.2 记忆示例

```
<平时><我><学><编程><喜欢><有收获>
<有时候><我><用><编程><做><游戏>
<经常><我><看><美剧><学英语><学会了>
```

---

## 三、编码器架构

### 3.1 InterestEncoder

```
输入：6个槽位的文本
      ["平时", "我", "学", "编程", "喜欢", "有收获"]
           ↓
┌─────────────────────────────────────┐
│ 1. 独立嵌入 → 6个槽位向量             │
│    每个槽位有独立嵌入表               │
│    [64] [64] [64] [64] [64] [64]    │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ 2. 多头自注意力 → 6个交互后的向量     │
│    维度间信息融合                     │
│    [64] [64] [64] [64] [64] [64]    │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│ 3. 拼接 → [384维] 记忆向量            │
│    [time'|sub'|act'|obj'|pur'|res'] │
└─────────────────────────────────────┘
```

### 3.2 参数量

| 模块 | 参数量 |
|------|--------|
| 6个槽位嵌入表 | ~12M |
| QKV投影 | ~0.15M |
| Transformer (2层) | ~0.13M |
| 输出投影 | ~0.15M |
| **总计** | **~12.5M** |

### 3.3 分槽特性

每个槽位对应向量中固定的分量区域：

```
[64维: time][64维: sub][64维: act][64维: obj][64维: pur][64维: res]
  0-63       64-127    128-191   192-255   256-319   320-383
```

这一特性是子空间检索的基础。

---

## 四、检索模式

### 4.1 三种检索模式

| 模式 | 做法 | 适用场景 |
|------|------|----------|
| **子空间检索** | 在指定槽位分量内做最近邻 | 精确约束过滤 |
| **全空间检索** | 整向量余弦相似度 | 语义模糊匹配 |
| **混合检索** | 子空间过滤 → 全空间排序 | 最优精度 |

### 4.2 子空间检索

**原理**：只在某个/某些槽位的分量子空间内做最近邻搜索。

```python
def subspace_search(memory_vectors, slot, value, top_k=10):
    """
    slot: 要检索的槽位名 (time/sub/act/obj/pur/res)
    value: 查询的槽位值文本
    """
    # 1. 用编码器提取查询的该槽位向量
    query_sub = encoder.encode_query(**{slot: value})
    query_sub = query_sub[slot_range[slot]]  # 提取对应分量

    # 2. 在该槽位子空间内计算余弦相似度
    scores = []
    for mem_vec in memory_vectors:
        mem_sub = mem_vec[slot_range[slot]]  # 记忆的该槽位分量
        score = cosine_similarity(query_sub, mem_sub)
        scores.append(score)

    # 3. 返回 top_k
    return top_k(scores, top_k)
```

### 4.3 全空间检索

**原理**：整向量余弦相似度，所有维度共同决定语义相关性。

```python
def fullspace_search(memory_vectors, query_vec, top_k=10):
    """整向量余弦相似度"""
    scores = []
    for mem_vec in memory_vectors:
        score = cosine_similarity(query_vec, mem_vec)
        scores.append(score)
    return top_k(scores, top_k)
```

### 4.4 混合检索（最优策略）

**原理**：先用子空间精确过滤，再用全空间语义排序。

```python
def hybrid_search(memory_vectors, query_slots, top_k=10):
    """
    query_slots: 要精确匹配的槽位字典
    例: {"subject": "我", "purpose": "喜欢"}
    """
    # 阶段1：子空间交集过滤
    candidates = set(range(len(memory_vectors)))

    for slot, value in query_slots.items():
        slot_results = subspace_search(memory_vectors, slot, value, top_k=1000)
        slot_ids = {r.id for r in slot_results}
        candidates &= slot_ids  # 取交集

    # 阶段2：全空间排序
    query_vec = encoder.encode_query(**query_slots)
    final_results = []

    for mem_id in candidates:
        score = cosine_similarity(query_vec, memory_vectors[mem_id])
        final_results.append((mem_id, score))

    # 返回 top_k
    return sorted(final_results, key=lambda x: x[1], reverse=True)[:top_k]
```

---

## 五、隐式图结构

### 5.1 图的形成

记忆之间不需要预先建立图关系，而是**通过查询自然涌现**。

```
记忆库：
  记忆1: <经常><我><学><编程><喜欢><有收获>
  记忆2: <有时候><我><用><编程><做><游戏>
  记忆3: <经常><我><看><美剧><学英语><学会了>
  记忆4: <平时><我><玩><游戏><喜欢><放松>

图涌现过程：

查询1: "学什么" → 子空间 act="学" → 找到记忆1,记忆3
                    ↓
查询2: "编程的其他关系" → 子空间 obj="编程" → 找到记忆2
                    ↓
隐式图形成：

  [学编程] ──act=学──> [用编程做游戏]
     │
     └──obj=美剧──> [看美剧] (通过purpose=学英语关联)

  [玩游戏] ──obj=游戏──> [用编程做游戏]
     │
     └──act=玩──> [学编程] (通过都含"游戏"关联)
```

### 5.2 图的特性

| 特性 | 说明 |
|------|------|
| 隐式 | 无需预先构建，查询时自然涌现 |
| 动态 | 每次查询可能形成不同的图 |
| 带权 | 边有权重（相似度分数） |
| 可遍历 | 支持多跳查询 |

### 5.3 多跳查询

```python
def multi_hop_query(seed_memory, max_hops=3):
    """从一条记忆出发，多跳遍历"""
    visited = {seed_memory.id}
    frontier = [seed_memory]
    edges = []  # 记录图的边

    for hop in range(max_hops):
        current = frontier.pop(0)

        # 对每个槽位探索关联记忆
        for slot in SLOTS:
            related = subspace_search(memory_vectors, slot, current[slot], top_k=3)

            for mem in related:
                if mem.id not in visited:
                    visited.add(mem.id)
                    frontier.append(mem)
                    edges.append({
                        "from": current.id,
                        "to": mem.id,
                        "slot": slot,
                        "value": current[slot]
                    })

    return {"nodes": visited, "edges": edges}
```

---

## 六、检索流程示例

### 6.1 查询 "喜欢什么"

```python
query_slots = {"subject": "我", "purpose": "喜欢"}

# 阶段1：子空间过滤
# subject="我" → 候选集A = {记忆1, 记忆2, 记忆4}
# purpose="喜欢" → 候选集B = {记忆1, 记忆4}
# 交集 = {记忆1, 记忆4}

# 阶段2：全空间排序
query_vec = encoder.encode_query(**query_slots)
# 对记忆1、记忆4 做全空间相似度排序

# 返回结果
# 1. <经常><我><学><编程><喜欢><有收获>
# 2. <平时><我><玩><游戏><喜欢><放松>
```

### 6.2 多跳查询 "学编程有什么用"

```python
# 第一跳：找关于"学编程"的记忆
hop1 = subspace_search(..., slot="object", value="编程")
# → <经常><我><学><编程><喜欢><有收获>

# 第二跳：从找到的记忆出发，探索关联
hop2 = multi_hop_query(hop1[0], max_hops=2)
# → <有时候><我><用><编程><做><游戏>
# → <平时><我><玩><游戏><喜欢><放松>

# 返回：编程可以用来做游戏
```

---

## 七、技术选型

### 7.1 编码器

- **模型**：InterestEncoder (12.5M 参数)
- **推理**：CPU 即可，<100ms

### 7.2 向量检索

- **库**：FAISS (CPU) 或 Chroma (支持元数据过滤)
- **索引**：HNSW (高速ANN索引)
- **候选集**：先用子空间过滤，再用全空间排序

### 7.3 检索延迟预估

```
10万记忆库：
- 子空间 ANN 检索 (FAISS HNSW): ~10ms
- 6个子空间交集: ~60ms
- 全空间排序 (top 100): ~5ms
- 总计: <100ms

100万记忆库：
- 子空间 ANN: ~50ms
- 交集 + 排序: ~30ms
- 总计: <100ms
```

---

## 八、总结

| 特性 | 说明 |
|------|------|
| **结构化** | 6槽位固定格式，向量分量对应明确 |
| **多模检索** | 子空间/全空间/混合三种模式 |
| **隐式图** | 无需预建图，查询时自然涌现 |
| **高效** | CPU 毫秒级检索 |
| **可扩展** | 记忆增加，图自动扩展 |

---

*文档版本：v1.0*
*最后更新：2026-04-15*
