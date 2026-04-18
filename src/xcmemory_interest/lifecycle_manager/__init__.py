"""
Lifecycle Manager - 生命周期决定与更新模块

核心组件：
- LifecycleManager: 生命周期管理器

注意：
- ProbabilitySampler 位于 vector_db.reranker，直接从那里导入使用
- 所有 Duration 计算（current_duration、interest_duration）均为解析计算，无需训练
- 概率融合使用 log-softmax + softmax（温度 T=2.0），完全确定性
"""

from .core import (
    LifecycleManager,
    LIFECYCLE_INFINITY,
)

# ProbabilitySampler 从 vector_db.reranker 导入
from ..vector_db.reranker import ProbabilitySampler

__all__ = [
    "LifecycleManager",
    "ProbabilitySampler",
    "LIFECYCLE_INFINITY",
]
