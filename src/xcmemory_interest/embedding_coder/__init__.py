"""
星尘记忆 - 查询句嵌入编码模块
包含：
- InterestEncoder: 兴趣嵌入生成模型
- QueryEncoder: 查询句原始嵌入生成
- QueryEncoderPipeline: 查询编码管道（使用原始嵌入）
- SlotTokenizer: 槽位分词器
- QuerySlots: 查询槽位数据类
"""
from .model import InterestEncoder, QueryEncoder, SLOT_NAMES, SLOT_DIM
from .query_encoder import (
    QueryEncoderPipeline,
    QuerySlots,
    SlotTokenizer,
    build_query_vector,
    parse_and_encode_query,
)

__all__ = [
    "InterestEncoder",
    "QueryEncoder",
    "QueryEncoderPipeline",
    "QuerySlots",
    "SlotTokenizer",
    "build_query_vector",
    "parse_and_encode_query",
    "SLOT_NAMES",
    "SLOT_DIM",
]
