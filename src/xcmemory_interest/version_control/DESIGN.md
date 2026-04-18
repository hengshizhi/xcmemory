# 单记忆版本控制设计

> 管理人：XC Memory Team
> 状态：✅ 已实现（v0.3.0）
> 优先级：P1

## 1. 概述

### 1.1 核心需求

在单个记忆（Memory）级别增加版本控制能力，支持：
- 每次更新自动记录历史版本（遵循存档阈值策略）
- 查看记忆的版本历史
- 回滚到指定版本
- 对比两个版本的差异
- 手动存档和批量存档
- 可视化面板管理

### 1.2 存档阈值策略

为防止数据库膨胀，采用存档阈值策略：

| 策略 | 说明 |
|------|------|
| **存档阈值** | 每 N 次更新才存档一次版本（默认 N=10） |
| **中间合并** | 未存档的更新合并到当前存档版本 |
| **强制存档** | `force_archive=True` 可强制存档当前版本 |
| **手动存档** | `archive(memory_id)` 手动存档 |
| **批量存档** | `archive_all()` 存档所有待存档记忆 |
| **最大版本数** | 每条记忆最多保留 50 个存档版本 |

### 1.3 数据模型

#### `memory_versions` 表

```sql
CREATE TABLE memory_versions (
    id TEXT PRIMARY KEY,              -- 版本记录ID，格式: ver_{memory_id}_{version}
    memory_id TEXT NOT NULL,          -- 关联的记忆ID
    version INTEGER NOT NULL,         -- 版本号（递增）
    query_sentence TEXT NOT NULL,     -- 查询句快照
    content TEXT,                      -- 记忆内容快照
    lifecycle INTEGER,                 -- 生命周期快照
    created_at TEXT NOT NULL,         -- 记忆创建时间
    updated_at TEXT NOT NULL,          -- 版本更新时间
    change_type TEXT NOT NULL,        -- 变更类型: CREATE/UPDATE/DELETE/ROLLBACK
    change_summary TEXT,               -- 变更摘要（可选）
    is_current INTEGER DEFAULT 0,     -- 是否当前版本 (1=当前, 0=历史)
    is_archived INTEGER DEFAULT 0,    -- 是否已存档 (1=存档, 0=待存档)
    update_sequence INTEGER DEFAULT 0, -- 更新序列号（用于判断是否触发存档）
    PRIMARY KEY (memory_id, version)
);
```

## 2. 架构设计

### 2.1 模块结构

```
version_control/
├── __init__.py           # 模块入口
├── version_manager.py    # 版本管理器核心（包含存档策略）
├── models.py             # 版本数据模型
└── DESIGN.md             # 本文档
```

### 2.2 核心类

```python
class VersionManager:
    """单记忆版本控制管理器（支持存档阈值策略）"""

    def __init__(
        self,
        vec_db: VecDBCRUD,
        archive_threshold: int = 10,      # 每N次更新存档一次
        max_versions_per_memory: int = 50, # 每条记忆最多保留版本数
    ):
        ...

    # --- 写入触发 ---
    def record_create(self, memory_id, memory, force_archive=False) -> str:
        """记录记忆创建（总是存档为v1）"""
        return version_id  # 或 None（如果未达到存档条件）

    def record_update(
        self,
        memory_id,
        old_memory,
        new_memory,
        force_archive=False,
    ) -> Optional[str]:
        """
        记录记忆更新（遵循存档阈值策略）

        - 未达到阈值：更新当前存档版本，不产生新版本
        - 达到阈值：创建新存档版本
        - force_archive=True：强制创建新存档版本
        """
        return version_id  # 或 None（如果未达到存档条件）

    def record_delete(self, memory_id) -> str:
        """记录记忆删除（总是存档）"""

    # --- 手动存档 ---
    def archive(self, memory_id) -> Optional[str]:
        """手动存档当前版本"""
        return version_id

    def archive_all() -> Dict[str, str]:
        """批量存档所有待存档记忆"""
        return {memory_id: version_id, ...}

    # --- 查询 ---
    def get_version_history(
        self,
        memory_id: str,
        limit: int = 10,
        archived_only: bool = False,
    ) -> List[MemoryVersion]:
        """获取版本历史"""

    def get_version(self, memory_id, version=None) -> Optional[MemoryVersion]:
        """获取指定版本（None=当前版本）"""

    def get_version_count(self, memory_id, archived_only=False) -> int:
        """获取版本总数"""

    def get_update_sequence(self, memory_id) -> int:
        """获取当前更新序列号"""

    def get_pending_updates_count(self, memory_id) -> int:
        """获取未存档的更新数"""

    # --- 回滚 ---
    def rollback(self, memory_id, target_version=None) -> bool:
        """回滚到指定版本"""

    # --- 对比 ---
    def diff_versions(self, memory_id, v1, v2=None) -> VersionDiff:
        """对比两个版本的差异"""

    # --- 清理 ---
    def prune_old_versions(self, memory_id, keep_count=5) -> int:
        """清理旧版本"""

    # --- 统计 ---
    def get_stats() -> Dict[str, Any]:
        """获取版本控制统计信息"""
```

### 2.3 版本数据模型

```python
@dataclass
class MemoryVersion:
    """记忆版本快照"""
    id: str
    memory_id: str
    version: int
    query_sentence: str
    content: str
    lifecycle: int
    created_at: datetime
    updated_at: datetime
    change_type: ChangeType  # CREATE/UPDATE/DELETE/ROLLBACK
    change_summary: str
    is_current: bool
    is_archived: bool = False  # 是否已存档

@dataclass
class VersionDiff:
    """版本差异"""
    memory_id: str
    from_version: int
    to_version: int
    changes: Dict[str, Tuple[Any, Any]]  # field -> (old, new)
    summary: str
```

## 3. VecDBCRUD 集成

### 3.1 初始化参数

```python
class VecDBCRUD:
    def __init__(
        self,
        persist_directory: str = "./data/xcmemory_db",
        vocab_size: int = 10000,
        archive_threshold: int = 10,           # 新增
        max_versions_per_memory: int = 50,      # 新增
    ):
        ...
```

### 3.2 update 方法

```python
def update(
    self,
    memory_id: str,
    content: Optional[str] = None,
    lifecycle: Optional[int] = None,
    force_archive: bool = False,  # 新增：强制存档
) -> bool:
    """
    更新记忆（遵循存档阈值策略）

    - 默认按阈值存档：每N次更新存档一次
    - force_archive=True：强制存档当前版本
    """
    old_memory = self._kv_read(memory_id)
    ok = self._kv_update(memory_id, content=content, lifecycle=lifecycle)

    if ok and old_memory:
        new_memory = self._kv_read(memory_id)
        self.version_manager.record_update(
            memory_id=memory_id,
            old_memory=old_memory,
            new_memory=new_memory,
            force_archive=force_archive,
        )
    return ok
```

## 4. 可视化面板

在 `visualizer/app.py` 中新增 **📜 版本控制** Tab：

### 4.1 功能区域

| 区域 | 功能 |
|------|------|
| **设置区** | 设置存档阈值、批量存档 |
| **历史查询** | 查看指定记忆的版本历史 |
| **详情对比** | 查看版本详情、对比差异 |
| **操作区** | 手动存档、回滚 |

### 4.2 界面元素

```
📜 版本控制
├── 设置区
│   ├── 存档阈值输入 (默认10)
│   ├── 设置阈值按钮
│   └── 存档所有按钮
├── 历史查询
│   ├── 记忆ID输入
│   ├── 显示数量滑块
│   └── 版本历史表格
├── 详情对比
│   ├── 版本号输入
│   ├── 查看详情按钮
│   ├── 对比版本输入
│   └── 对比差异按钮
└── 操作区
    ├── 记忆ID输入
    ├── 手动存档按钮
    ├── 回滚版本输入
    └── 回滚按钮
```

## 5. 使用示例

```python
from xcmemory_interest import VecDBCRUD

# 初始化（存档阈值=5，每5次更新存档一次）
db = VecDBCRUD(
    persist_directory="./data/memory",
    archive_threshold=5,
)
vm = db.version_manager

# 写入记忆（自动存档为v1）
memory_id = db.write(
    query_sentence="<时间><主体><动作><对象><目的><结果>",
    content="记忆内容",
    lifecycle=30,
)

# 更新（按阈值存档）
for i in range(10):
    db.update(memory_id, content=f"更新{i+1}")

# 手动强制存档
vm.archive(memory_id)

# 批量存档
results = vm.archive_all()

# 查看版本历史
history = vm.get_version_history(memory_id, limit=20)
for v in history:
    print(f"v{v.version} [{v.change_type.value}] {v.change_summary}")

# 对比差异
diff = vm.diff_versions(memory_id, v1=1, v2=3)
print(diff.format_diff())

# 回滚
vm.rollback(memory_id, target_version=1)

# 获取统计
stats = vm.get_stats()
print(f"总版本数: {stats['total_versions']}")
print(f"待存档更新: {stats['pending_archive_count']}")
```

## 6. 存储成本控制

| 控制机制 | 说明 |
|----------|------|
| **存档阈值** | 默认每10次更新存档一次，大幅减少版本数 |
| **最大版本数** | 每条记忆最多50个存档版本 |
| **自动清理** | 超过最大版本数时自动删除最旧版本 |
| **中间合并** | 未存档的更新合并到当前存档版本 |

### 成本估算

假设存档阈值=10，10万条记忆，平均更新100次：
- 每条记忆约 100/10 = 10 个存档版本
- 每个版本约 500 字节
- 额外存储：100,000 × 10 × 500 = 500MB

相比每次更新都存档（100万版本，5GB），空间节省 90%。

## 7. 向后兼容

- 现有 `memories` 表结构不变
- `record_version` 参数保留（现已改为 `force_archive`）
- 已有的版本数据自动迁移到新表结构