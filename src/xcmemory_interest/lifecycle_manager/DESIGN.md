# 生命周期决定与更新模块

> 管理人：XC Memory Team
> 状态：设计完成（实现已同步 v3：支持非兴趣模式降级）

## 1. 概述

### 1.1 核心职责

- **生命周期决定**：写入新记忆时，根据查询句内容和相关记忆的被动回忆结果，智能决定记忆的存活时长
- **生命周期更新**：当写入新记忆时，同时更新相关老记忆的生命周期

### 1.2 生命周期表示

**实际存储在 VecDBCRUD 的 `memories` 表中（单字段）**：

```sql
-- memories 表的 lifecycle 字段
lifecycle INTEGER NOT NULL  -- 有效期天数（TTL）
```

> 设计历史：早期方案曾设计 `lifecycle_start` + `lifecycle_duration` 两列，
> 实践中简化为单字段，配合 `created_at` 计算过期时间：
> `expires_at = created_at + lifecycle_days * 86400`

**辅助 `lifecycles` 表（可选）**：用于更复杂的生命周期管理，非必需。

### 1.3 架构位置

```
┌──────────────────────────────────────────────────────────────────────┐
│                        MemorySystem                                    │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────────────┐ │
│  │  VecDBCRUD │  │  SQLDatabase│  │     LifecycleManager           │ │
│  │  (vec_db)  │  │  (可选辅助)  │  │                                │ │
│  │  • search  │  │             │  │  ┌──────────────────────────┐  │ │
│  │  • memories│  │             │  │  │ • decide_new()           │  │ │
│  └────────────┘  └────────────┘  │  │ • update_existing()      │  │ │
│           │                │      │  │ • ProbabilitySampler     │  │ │
│           ▼                ▼      │  └──────────────────────────┘  │ │
│  ┌────────────────────────────────┐│                                │ │
│  │  InterestEncoder (自注意力)     ││                                │ │
│  │  • encode_query_with_ids_slots  ││                                │ │
│  │  → 各槽位经注意力处理后的向量   ││                                │ │
│  └────────────────────────────────┘│                                │ │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. 生命周期决定（新数据）

### 2.1 核心思想

新记忆的生命周期由**三种 Duration** 加权融合而成：

| Duration | 说明 | 计算方式 |
|----------|------|---------|
| **current_d** | 基于被动回忆结果的持续性 | 生命周期 + 采样权重 → 加权平均 → DurationNetwork |
| **interest_d** | 基于查询句的兴趣强度 | 经自注意力处理的槽位向量 L2 范数之和 → DurationNetwork |
| **reference_d** | 用户提供的参考生命周期 | 直接传入 |

**融合前对三种 Duration 做 log-softmax 归一化**（消除量纲差异），最终 `lifecycle = Σ(p_i * duration_i)`。

### 2.2 计算流程

```
写入新数据
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. 被动回忆（Passive Recall）                                  │
│    • 用新数据查询句进行 top_k 最近邻搜索                       │
│    • 使用 ProbabilitySampler.sample() 随机采样                 │
│    • 获取: memory_ids, lifecycles, sample_weights (未归一化)  │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 计算 current_duration                                      │
│    输入: lifecycles[], sample_weights[]                       │
│    权重归一化 → 加权平均 → DurationNetwork → current_d       │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 计算 interest_duration                                     │
│    • encode_query_with_ids_slots() 获取经注意力处理的槽位向量  │
│    • 各槽位向量的 L2 范数之和 → DurationNetwork → interest_d │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 概率融合                                                   │
│    • duration_inputs: {current_d, interest_d, reference_d}   │
│    • log-softmax 归一化 → ProbabilityFusionNetwork → 概率   │
│    • lifecycle = Σ(p_i * duration_i)                       │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 数学表达

```
new_lifecycle = p_current * current_d
             + p_interest * interest_d
             + p_reference * reference_d

其中 [p_current, p_interest, p_reference] = softmax(log_softmax([current_d, interest_d, reference_d]) * T)，T=2.0（温度参数，可调）
```

> 注：输入经过 log-softmax 归一化，解决不同量纲（如生命周期天数 vs 注意力分数）导致的数值差异。

---

## 3. 生命周期更新（老数据）

### 3.1 核心思想

当写入新记忆时，被动回忆结果中的老记忆也需要更新其生命周期。
老记忆的新生命周期由**自身生命周期**和**新记忆目标值**共同决定，离群点几乎不变，典型记忆逐步趋同。

### 3.2 计算公式

```
ratio = old_lc / ref_lc
f = sqrt(ratio)       -- 老记忆比 ref_lc 弱时 f < 1（削弱），强时 f > 1（增强）
w = sampled_prob      -- 老记忆的采样权重（离群点 w 小，典型记忆 w 大）

new_lc = old_lc * (1 - w) + ref_lc * f * w
```

其中 `ref_lc`（参考目标值）由概率融合得出：
```
ref_lc = fuse(interest_d_old, current_d_old, interest_d_new)
```

### 3.3 融合输入（三要素）

| Duration | 说明 |
|----------|------|
| **interest_d_old** | 老记忆查询句的兴趣强度 |
| **current_d_old** | 老记忆的被动回忆持续性 |
| **interest_d_new** | 新记忆查询句的兴趣强度 |

### 3.4 计算流程

```
写入新数据（触发老数据更新）
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. 被动回忆 + 筛选 lifecycle ≠ ∞ 的记忆                       │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 预计算 interest_duration_new（新数据的兴趣强度）              │
│    所有老记忆共用同一个 interest_duration_new                  │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 遍历每条老记忆:                                           │
│    a. 获取老记忆的查询句 → interest_duration_old             │
│    b. 收集所有老记忆 + 新记忆生命周期 → current_duration_old │
│    c. 概率融合 → ref_lc                                     │
│    d. 计算 f = sqrt(old_lc / ref_lc)，w = sampled_prob      │
│    e. new_lc = old_lc * (1 - w) + ref_lc * f * w           │
│    f. 若 new_lifecycle ≠ old_lifecycle，则更新             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 访问触发的生命周期增长

### 4.1 核心思想

当记忆被访问时，自动联想相关记忆并更新其生命周期。
访问触发比写入触发更微弱——增量通过 Sigmoid 导数函数压缩，使增长越来越缓慢。

### 4.2 增长阶段

| 阶段 | 范围 | Sigmoid 中点 | 特征 |
|------|------|-------------|------|
| **短期记忆** | 0 < old_lc < 7天 | 3.5天 | 快速饱和 |
| **长期记忆** | 7天 <= old_lc < 30天 | 18.5天 | 缓慢饱和 |
| **跃迁** | old_lc >= 30天 | - | 直接跃迁到永不过期 |

### 4.3 计算流程

```
记忆被访问
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. 以被访问记忆的查询句为中心进行被动回忆                     │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 复用 _update_existing_lifecycles 同款公式                │
│    interim_new_lc = old_lc * (1-w) + accessed_lc * f * w  │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 计算 actual_delta（Sigmoid 导数衰减）                    │
│    scale = sigmoid_deriv(t, mid, k) with floor            │
│    actual_delta = delta * scale                           │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 跃迁检查                                                 │
│    if old_lc >= 30天: return infinity                     │
│    else: return old_lc + actual_delta                     │
└─────────────────────────────────────────────────────────────┘
```

### 4.4 Sigmoid 导数保底

```
sigmoid_deriv(t; mid, k) = k * sigmoid(k*(t-mid)) * (1 - sigmoid(k*(t-mid)))

带保底版本：
scale = min_scale + (max_deriv - min_scale) * (deriv / max_deriv)
       = 0.1 + (k/4 - 0.1) * (deriv / (k/4))

效果：
- 在 mid 处：scale ≈ k/4（最大增长）
- 在两端：scale ≈ 0.1（保底 10%）
```

### 4.5 参数常量

| 参数 | 默认值 | 说明 |
|------|--------|------|
| SHORT_TERM_CAP | 7天 | 短期记忆上限 |
| LONG_TERM_CAP | 30天 | 长期记忆上限 |
| TRANSITION_CAP | 365天 | 跃迁临界值 |
| MIN_SCALE | 0.1 | Sigmoid 导数保底值 |

---

## 5. ProbabilitySampler 语义澄清

**采样权重 = a * f(distance)**，其中 f 是正态分布概率密度函数。

- 这是**未归一化的权重**（weight），不是严格意义上的概率（sum != 1）
- 使用多项式采样（replace=True）从归一化权重中抽取 n_select 个候选
- 距离越近的候选，被选中的概率越高

---

## 5. API 设计

### 5.1 LifecycleManager 类

```python
class LifecycleManager:
    """
    生命周期管理器

    职责：
    1. 写入新记忆时决定其生命周期 (decide_new_lifecycle)
    2. 写入新记忆时自动更新相关老记忆的生命周期（内部调用 _update_existing_lifecycles）
    3. 访问记忆时自动更新相关记忆的生命周期 (on_memory_accessed)

    依赖：
    - vec_db: VecDBCRUD 实例（用于向量搜索、获取记忆的查询句）
    - sql_db: SQLDatabase 实例（用于辅助生命周期表，可选）
    """

    # 生命周期常量
    LIFECYCLE_INFINITY: int = 999999  # 永不过期的生命周期
    SHORT_TERM_CAP: int = 7 * 86400  # 短期记忆上限：7天
    LONG_TERM_CAP: int = 30 * 86400  # 长期记忆上限：30天
    TRANSITION_CAP: int = 365 * 86400  # 跃迁临界值：365天
    MIN_SCALE: float = 0.1  # Sigmoid 导数保底值

    def __init__(
        self,
        vec_db: VecDBCRUD,
        sql_db: SQLDatabase = None,
        top_k: int = 20,
        sample_size: int = 5,
        sigma: float = None,
    ):
        """
        初始化 LifecycleManager

        Args:
            vec_db: VecDBCRUD 实例（向量数据库操作对象）
            sql_db: SQLDatabase 实例（SQL数据库操作对象，可选）
            top_k: 被动回忆时检索的候选数量
            sample_size: 被动回忆后采样的记忆数量
            sigma: 概率采样的正态分布标准差（None=自适应）
        """

    # =================================================================
    # 核心 API
    # =================================================================

    def decide_new_lifecycle(
        self,
        query_slots: Dict[str, str],
        reference_duration: int,
    ) -> int:
        """
        决定新记忆的生命周期

        Args:
            query_slots: 查询句槽位 {"subject": "我", "action": "学习", ...}
            reference_duration: 参考生命周期（用户指定或默认）

        Returns:
            计算得出的生命周期值（有效期秒数）
        """

    def _update_existing_lifecycles(
        self,
        candidates,
        interest_duration_new: float,
        new_lifecycle: int,
    ) -> List[Tuple[str, int, int]]:
        """
        更新被动回忆中相关老记忆的生命周期（私有方法，由 decide_new_lifecycle 自动调用）

        Args:
            candidates: 被动回忆结果
            interest_duration_new: 新记忆的查询句兴趣强度
            new_lifecycle: 新记忆的生命周期

        Returns:
            [(memory_id, old_lifecycle, new_lifecycle), ...] 更新详情
        """

    def on_memory_accessed(
        self,
        memory_id: str,
    ) -> List[Tuple[str, int, int]]:
        """
        当记忆被访问时，自动更新相关记忆的生命周期

        以被访问记忆为中心进行被动回忆，套用 _update_existing_lifecycles
        同款公式计算目标值，但增量通过 Sigmoid 导数衰减。

        增长阶段：
        - 0 < old_lc < 7天：使用短期 Sigmoid 导数
        - 7天 <= old_lc < 30天：使用长期 Sigmoid 导数
        - old_lc >= 30天：跃迁到永不过期

        Args:
            memory_id: 被访问的记忆 ID

        Returns:
            [(memory_id, old_lifecycle, new_lifecycle), ...] 更新详情
        """

    # =================================================================
    # 辅助 API
    # =================================================================

    def get_memory_lifecycle(self, memory_id: str) -> Optional[int]:
        """获取记忆的生命周期"""

    def set_memory_lifecycle(self, memory_id: str, lifecycle: int) -> bool:
        """设置记忆的生命周期"""

    def is_infinite_lifecycle(self, lifecycle: int) -> bool:
        """判断是否为永不过期的生命周期"""

    def get_probability_sampler(self) -> ProbabilitySampler:
        """获取概率采样器"""

    # =================================================================
    # 生命周期过期管理 API
    # =================================================================

    def is_expired(self, memory_id: str, current_time: int = None) -> bool:
        """
        判断记忆是否已过期

        过期条件: created_at + lifecycle_days * 86400 < current_time
        永不过期: lifecycle >= LIFECYCLE_INFINITY

        Args:
            memory_id: 记忆 ID
            current_time: 当前时间戳（Unix epoch），None=使用当前时间

        Returns:
            是否已过期
        """

    def filter_expired(self, memory_ids: List[str], current_time: int = None) -> List[str]:
        """过滤出已过期的记忆 ID 列表"""

    def filter_alive(self, memory_ids: List[str], current_time: int = None) -> List[str]:
        """过滤出未过期的记忆 ID 列表"""

    def delete_expired(
        self,
        memory_ids: List[str] = None,
        dry_run: bool = False,
    ) -> List[str]:
        """
        删除过期的记忆

        Args:
            memory_ids: 要检查的记忆 ID 列表，None=检查所有记忆
            dry_run: True=只返回待删除列表，不实际删除

        Returns:
            已删除（或待删除）的记忆 ID 列表
        """

    def check_and_cleanup_all(self, batch_size: int = 100) -> Dict[str, int]:
        """
        检查所有记忆的生命周期，延时删除过期数据

        这是一个批处理方法，用于定期清理。

        Returns:
            {"total": 总数, "expired": 已过期数量, "deleted": 已删除数量, "remaining": 剩余数量}
        """

    def get_readable_and_cleanup(
        self,
        memory_ids: List[str],
        current_time: int = None,
    ) -> List[str]:
        """
        读取记忆时检查并删除过期数据（延时删除）

        Returns:
            未过期的记忆 ID 列表（已过期的已被删除）
        """

    def get_all_lifecycles(self) -> List[Dict[str, Any]]:
        """
        获取所有记忆的生命周期信息

        Returns:
            [{"memory_id": id, "lifecycle": lc, "created_at": ts}, ...]
        """
```

### 5.2 ProbabilitySampler（概率采样器）

```python
class ProbabilitySampler:
    """
    基于距离正态分布的概率采样器

    用于被动回忆时的随机采样：
    1. top_k(n): 用较大 n 检索候选集
    2. 基于距离计算采样权重 w = a * f(distance)
    3. 按权重随机采样 N 个（多项式采样，replace=True）

    设计：
    - 距离近的候选被选中概率高
    - sigma 控制分布宽度（自适应或指定）
    - 返回的 sample_weight 是未归一化权重，不是概率
    """

    def __init__(self, sigma: float = None, random_seed: int = None):
        """
        Args:
            sigma: 正态分布标准差
                  - None: 自适应（使用平均距离 * 0.5）
                  - 小值：尖锐分布（只采样很近的）
                  - 大值：平坦分布（采样范围更广）
            random_seed: 随机种子（用于可重现性）
        """

    def sample(
        self,
        candidates: List[Dict[str, Any]],
        distances: List[float],
        n_select: int,
    ) -> List[Dict[str, Any]]:
        """
        概率采样

        Args:
            candidates: 候选列表（包含 memory_id, lifecycle 等字段）
            distances: 各候选的距离
            n_select: 采样数量

        Returns:
            采样的候选列表，每项含 sample_weight（未归一化权重）和 distance 字段
        """
```

### 5.3 DurationNetwork（Duration 计算网络）

```python
class DurationNetwork(nn.Module):
    """
    Duration 计算小网络

    将生命周期值或注意力分数转换为 Duration 表示。
    这是一个简单的 MLP 网络（输入 1 维 → 隐藏 16 维 → 输出 1 维）。
    """

    def __init__(self, input_dim: int = 1, hidden_dim: int = 16, output_dim: int = 1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
            nn.Softplus(),  # 输出非负
        )
```

### 5.4 ProbabilityFusionNetwork（概率融合网络）

```python
class ProbabilityFusionNetwork(nn.Module):
    """
    概率融合网络

    将三种 Duration 输入，输出三种概率（和为1）。

    输入: [current_d, interest_d, reference_d] (3维，已 log-softmax 归一化)
    输出: [p_current, p_interest, p_reference] (3维，和=1)

    注意：输入的 Duration 值在融合前会经过 log-softmax 归一化，
    以消除不同量纲（如生命周期天数 vs 注意力分数）导致的数值差异。
    """

    def __init__(self, hidden_dim: int = 32):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(3, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, 3),
            nn.Softmax(dim=-1),  # 概率输出，和=1
        )
```

---

## 6. 过期判断逻辑

```
is_expired(memory_id):
    if memory not found → True（不存在视为过期）

    if lifecycle >= LIFECYCLE_INFINITY → False（永不过期）

    expires_at = created_at + lifecycle_days * 86400
    return current_time >= expires_at
```

---

## 7. 与 PyAPI 的集成

### 7.1 MemorySystem 初始化时创建

```python
class MemorySystem:
    def __init__(self, ...):
        self._lifecycle_manager: Optional[LifecycleManager] = None

    def set_lifecycle_manager(self, lifecycle_manager: LifecycleManager):
        """设置生命周期管理器"""
        self._lifecycle_manager = lifecycle_manager

    def write(self, query_slots, content, reference_lifecycle=None, ...):
        # 1. 决定生命周期
        if self._lifecycle_manager is not None:
            lifecycle = self._lifecycle_manager.decide_new_lifecycle(
                query_slots=query_slots,
                reference_duration=reference_lifecycle or 2592000,  # 默认30天=2592000秒
            )
        else:
            lifecycle = reference_lifecycle or 2592000  # 默认30天=2592000秒

        # 2. 执行写入
        memory_id = self._vec_db.write(
            query_sentence=...,
            content=content,
            lifecycle=lifecycle,
            ...
        )

        # 3. 更新相关老记忆的生命周期（由 decide_new_lifecycle 内部自动完成）
        # 无需显式调用，decide_new_lifecycle 会自动更新被动回忆到的老记忆

        return memory_id
```

### 7.2 PyAPI 创建记忆系统

```python
class PyAPI:
    def create_system(self, name, initialize=True, enable_lifecycle=True):
        system = MemorySystem(...)
        if initialize:
            system.initialize()
            if enable_lifecycle:
                lifecycle_manager = LifecycleManager(
                    vec_db=system._vec_db,
                    sql_db=system._sql_db,
                    top_k=20,
                    sample_size=5,
                )
                system.set_lifecycle_manager(lifecycle_manager)
        return system
```

---

## 8. 使用示例

### 8.1 基础使用

```python
from xcmemory_interest.pyapi import PyAPI
from xcmemory_interest.lifecycle_manager import LifecycleManager

# 创建 PyAPI
pyapi = PyAPI("./data/xcmemory")

# 创建记忆系统（启用生命周期管理）
system = pyapi.create_system("test", enable_lifecycle=True)

# 写入记忆（自动决定生命周期）
memory_id = system.write(
    query_sentence="<平时><我><学习><编程><为了><进步>",
    content="我学习编程是为了进步",
    reference_lifecycle=2592000,  # 30天=2592000秒
)

# 查看记忆的生命周期
lifecycle = system.get_memory_lifecycle(memory_id)
print(f"记忆生命周期: {lifecycle} 秒")
```

### 8.2 直接使用 LifecycleManager

```python
from xcmemory_interest.basic_crud import VecDBCRUD
from xcmemory_interest.auxiliary_query import SQLDatabase
from xcmemory_interest.lifecycle_manager import LifecycleManager

vec_db = VecDBCRUD("./data/vec_db")
sql_db = SQLDatabase("./data/aux_db", db_name="lifecycle")

lifecycle_mgr = LifecycleManager(
    vec_db=vec_db,
    sql_db=sql_db,
    top_k=20,
    sample_size=5,
)

# 决定新记忆的生命周期
query_slots = {"subject": "我", "action": "学习", "purpose": "提升"}
lifecycle = lifecycle_mgr.decide_new_lifecycle(
    query_slots=query_slots,
    reference_duration=2592000,  # 30天=2592000秒
)

# 老记忆的生命周期会在 decide_new_lifecycle 内部自动更新
# 如需获取更新详情，通过事件回调或返回值扩展实现（当前版本不返回更新详情）
```

### 8.3 生命周期过期管理

```python
# 检查哪些记忆已过期
expired_ids = lifecycle_mgr.filter_expired(["mem_001", "mem_002", "mem_003"])
print(f"已过期: {expired_ids}")

# 获取未过期的记忆
alive_ids = lifecycle_mgr.filter_alive(["mem_001", "mem_002", "mem_003"])
print(f"未过期: {alive_ids}")

# 读取时自动清理过期数据
readable_ids = lifecycle_mgr.get_readable_and_cleanup(["mem_001", "mem_002", "mem_003"])

# 批量清理所有过期数据
result = lifecycle_mgr.check_and_cleanup_all(batch_size=100)
print(f"清理结果: {result}")
```

---

## 9. 待讨论

- [x] Duration 计算网络的结构和参数（已改为解析计算：加权平均 + L2范数，无需 MLP）
- [x] 概率融合网络的训练方式（已改为解析计算：softmax(log_softmax) 融合，无需训练）
- [x] 生命周期的单位：秒（已确认，天数已改为秒）
- [x] 与 TimeIndex 的联动：时间词是否影响生命周期（待后续扩展）
- [x] 无限生命周期的处理策略（lifecycle >= 999999 视为永不过期）
- [x] 融合网络的 log-softmax 归一化（已添加，解决量纲差异）
- [x] 老记忆生命周期更新的惯性公式（已改为 new_lc = old_lc*(1-w) + ref_lc*f*w）
- [x] 访问触发的生命周期增长（已实现：Sigmoid 导数衰减 + 两阶段跃迁）
