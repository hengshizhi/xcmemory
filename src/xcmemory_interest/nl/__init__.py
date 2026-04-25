"""
星尘自然语言处理模块 (nl)

提供自然语言到记忆系统的智能转换能力，包括：
- 意图识别 (Intent Classifier)
- NL → INSERT MQL 生成器
- NL → SELECT MQL 生成器
- 查询重写 (Query Rewriter) - 已废弃，由 IntentClassifier 替代
- 检索充分性检查 (Sufficiency Checker)
- 6槽记忆提取 (Slot Extractor)
- 混合检索 (Hybrid Search) - 延迟导入
- LLM 重排序 (LLM Ranker)
- NL Pipeline 编排引擎 (NLPipeline)
- 去重与记忆强化 (Reinforcement)
"""

from .decision import NLQueryDecider
from .rewriter import QueryRewriter
from .intent_classifier import IntentClassifier
from .write_mql_generator import WriteMQLGenerator
from .sufficiency import SufficiencyChecker
from .mql_generator import MQLGenerator
from .slot_extractor import SlotExtractor, SlotValidator
from .ranker import MemoryItemRanker
from .pipeline import NLPipeline, NLSearchPipeline
from .reinforcement import (
    compute_content_hash,
    compute_recency_decay,
    compute_salience_score,
    ReinforcementMixIn,
    # 注意: extend_vec_db_crud 和 extend_memory_system 会触发 torch 导入
    # 请在需要时手动调用
)


def __getattr__(name):
    """延迟导入 HybridSearch，避免 torch 依赖"""
    if name == "HybridSearch":
        from .hybrid_search import HybridSearch
        return HybridSearch
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "NLQueryDecider",
    "QueryRewriter",
    "IntentClassifier",
    "WriteMQLGenerator",
    "SufficiencyChecker",
    "MQLGenerator",
    "SlotExtractor",
    "SlotValidator",
    "HybridSearch",  # 延迟导入
    "MemoryItemRanker",
    "NLPipeline",
    "NLSearchPipeline",
    # Reinforcement
    "compute_content_hash",
    "compute_recency_decay",
    "compute_salience_score",
    "ReinforcementMixIn",
]
