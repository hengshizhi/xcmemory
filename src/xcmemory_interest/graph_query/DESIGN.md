# 图查询模块

> 管理人：TODO
> 状态：待实现

## 职责

提供基于隐式图的查询能力，支持多跳推理。

## 核心概念

**隐式图**：图不是预先存储的，而是从记忆库中**查询时动态涌现**的。

```
记忆库中的记忆通过槽位值共享隐式连接：

  记忆A: <经常><我><学><编程><喜欢><有收获>
                            ↑
                      共享"编程"
                            ↑
  记忆B: <有时候><我><用><编程><做><游戏>

  → 图边: 记忆A --(slot=object, value=编程)--> 记忆B
```

---

## API 设计

```python
class GraphQuery:
    """图查询模块

    图不预建，查询时通过子空间检索动态构建。
    """

    def find_related(
        self,
        memory_id: str,
        slot: str,  # 沿哪个槽位扩展
        top_k: int = 5,
    ) -> List[RelatedMemory]:
        """给定一条记忆，沿指定槽位找关联记忆

        例：给定"学编程"记忆，沿 object="编程" 找其他记忆
        """

    def multi_hop(
        self,
        seed_memory_id: str,
        max_hops: int = 3,
        slots: List[str] = None,  # 沿哪些槽位扩展，None=全部
    ) -> Graph:
        """多跳图查询

        从种子记忆出发，N跳遍历所有关联记忆。
        返回动态构建的图结构。
        """

    def find_path(
        self,
        from_memory_id: str,
        to_memory_id: str,
        max_hops: int = 3,
    ) -> Optional[Path]:
        """找两条记忆之间的最短路径"""
```

---

## 图的数据结构

```python
@dataclass
class Graph:
    nodes: List[str]  # memory_ids
    edges: List[Edge]

@dataclass
class Edge:
    from_id: str
    to_id: str
    slot: str  # 通过哪个槽位连接
    value: str  # 槽位值
    weight: float  # 相似度分数
```

---

## 与子空间检索的关系

图查询本质是**连续的子空间检索**：

```
多跳查询 = 反复执行：
  1. 子空间检索（固定槽位）
  2. 收集结果
  3. 扩展边界
```

---

## 待实现

- [ ] 循环检测（避免死循环）
- [ ] 边权重融合策略
- [ ] 路径排序（最短/最相关）
- [ ] 与主检索模块的集成
