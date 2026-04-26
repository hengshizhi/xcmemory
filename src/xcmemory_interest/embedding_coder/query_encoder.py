"""
星尘记忆 - 查询词编码模块
Query Word Encoding for Structured Memory Retrieval

查询词编码流程：
1. 查询词的各个槽位分别转化成嵌入（6个嵌入向量）
2. 6个嵌入向量作为token进入 InterestEncoder（自注意力）
3. InterestEncoder 输出6个经过兴趣模型处理的token
4. 6个输出向量拼装成384维存入向量数据库

与记忆编码流程完全一致，保证查询和记忆在同一向量空间。
"""

import torch
import torch.nn as nn
import numpy as np
from typing import Optional, Dict, List, Union, Any, Tuple
from dataclasses import dataclass

from .model import InterestEncoder, QueryEncoder, SLOT_NAMES, SLOT_DIM


@dataclass
class QuerySlots:
    """查询槽位数据类"""
    scene: Optional[torch.Tensor] = None
    subject: Optional[torch.Tensor] = None
    action: Optional[torch.Tensor] = None
    object: Optional[torch.Tensor] = None
    purpose: Optional[torch.Tensor] = None
    result: Optional[torch.Tensor] = None  # 查询时通常不知道结果

    def to_dict(self) -> Dict[str, Optional[torch.Tensor]]:
        return {
            "scene": self.scene,
            "subject": self.subject,
            "action": self.action,
            "object": self.object,
            "purpose": self.purpose,
            "result": self.result,
        }

    def get_filled_slots(self) -> List[str]:
        """返回已填充的槽位名称列表"""
        return [slot for slot in SLOT_NAMES if self.to_dict().get(slot) is not None]

    def get_empty_slots(self) -> List[str]:
        """返回未填充的槽位名称列表"""
        return [slot for slot in SLOT_NAMES if self.to_dict().get(slot) is None]


class QueryEncoderPipeline:
    """查询编码管道

    查询词编码流程（与记忆编码一致）：
    1. 各槽位分别查嵌入表 → 6个嵌入向量 [64]
    2. 6个嵌入向量进入 InterestEncoder（自注意力）
    3. InterestEncoder 输出6个经过处理的向量 [64] × 6
    4. 拼接 → [384] 用于检索

    与 InterestEncoder 共享嵌入表，保证查询和记忆在同一向量空间。
    """

    def __init__(
        self,
        interest_encoder: InterestEncoder,
        device: str = "cpu",
    ):
        """
        Args:
            interest_encoder: 已训练的 InterestEncoder（共享嵌入表）
            device: 计算设备
        """
        self.device = device
        self.encoder = interest_encoder
        self.encoder.eval()

    def encode(
        self,
        slots: QuerySlots,
        use_raw: bool = True,
        normalize: bool = True,
    ) -> np.ndarray:
        """
        编码查询向量

        流程：
        - use_raw=False: 槽位嵌入 → InterestEncoder自注意力 → 6输出向量拼接 → [384]
        - use_raw=True: 槽位嵌入 → 直接拼接 → [384]

        Args:
            slots: 查询槽位数据
            use_raw: 是否使用原始嵌入（不过自注意力）
            normalize: 是否L2归一化

        Returns:
            [384] 查询向量
        """
        slot_dict = slots.to_dict()

        with torch.no_grad():
            # 构建 [6, seq_len] 的 token_ids
            token_ids = self._build_token_ids(slot_dict)

            if use_raw:
                # 原始嵌入：直接拼接，不过自注意力
                vector = self.encoder.encode_raw(token_ids)
            else:
                # 兴趣嵌入：经过自注意力处理
                query_slots = {
                    "scene": slot_dict.get("scene"),
                    "subject": slot_dict.get("subject"),
                    "action": slot_dict.get("action"),
                    "object": slot_dict.get("object"),
                    "purpose": slot_dict.get("purpose"),
                }
                vector = self.encoder.encode_query_with_ids(**query_slots)

            vector = vector.cpu().numpy()

            if normalize:
                norm = np.linalg.norm(vector)
                if norm > 0:
                    vector = vector / norm

        return vector

    def encode_with_intermediate(
        self,
        slots: QuerySlots,
        normalize: bool = True,
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray], np.ndarray]:
        """
        编码查询向量，同时返回中间结果

        Args:
            slots: 查询槽位数据
            normalize: 是否L2归一化

        Returns:
            (final_vector, slot_vectors, raw_vectors)
            - final_vector: [384] 最终查询向量
            - slot_vectors: {slot: [64]} 经注意力处理后的槽位向量
            - raw_vectors: {slot: [64]} 原始嵌入（未经注意力）
        """
        slot_dict = slots.to_dict()

        with torch.no_grad():
            token_ids = self._build_token_ids(slot_dict)

            # 原始嵌入：6个槽位向量直接查表
            raw_vectors = {}
            for i, slot in enumerate(SLOT_NAMES):
                tensor = slot_dict.get(slot)
                if tensor is not None:
                    if tensor.dim() == 1:
                        tensor = tensor.unsqueeze(0)
                    emb = self.encoder.slot_embeddings[slot](tensor)
                    vec = emb.mean(dim=1).squeeze(0).cpu().numpy()  # [64]
                else:
                    vec = np.zeros(SLOT_DIM)
                raw_vectors[slot] = vec

            # 经注意力处理后的向量
            query_slots = {
                "scene": slot_dict.get("scene"),
                "subject": slot_dict.get("subject"),
                "action": slot_dict.get("action"),
                "object": slot_dict.get("object"),
                "purpose": slot_dict.get("purpose"),
            }
            # 需要用特殊方式获取中间结果，这里用 encode_query_with_ids
            # 它的输出顺序是 scene, subject, action, object, purpose, result (6个槽位)
            # 但 encode_query 只处理前5个槽位
            full_query_slots = {
                "scene": slot_dict.get("scene"),
                "subject": slot_dict.get("subject"),
                "action": slot_dict.get("action"),
                "object": slot_dict.get("object"),
                "purpose": slot_dict.get("purpose"),
            }
            final_vector = self.encoder.encode_query_with_ids(**full_query_slots)
            final_vector = final_vector.cpu().numpy()

            # 构建槽位向量字典（从最终向量中提取）
            slot_vectors = {}
            for i, slot in enumerate(SLOT_NAMES):
                slot_vectors[slot] = final_vector[i * SLOT_DIM:(i + 1) * SLOT_DIM]

            if normalize:
                norm = np.linalg.norm(final_vector)
                if norm > 0:
                    final_vector = final_vector / norm
                    for slot in slot_vectors:
                        slot_vectors[slot] = slot_vectors[slot] / norm

        return final_vector, slot_vectors, raw_vectors

    def _build_token_ids(
        self,
        slot_dict: Dict[str, Optional[np.ndarray]],
        pad_token_id: int = 0,
    ) -> torch.Tensor:
        """从槽位字典构建 token_ids 张量

        Args:
            slot_dict: 槽位字典（可以是 numpy 数组或 torch 张量）
            pad_token_id: 填充 token 的 id

        Returns:
            [6, max_seq_len] 所有槽位填充到相同长度
        """
        # 收集所有有效序列
        valid_tensors = []
        for slot in SLOT_NAMES:
            tensor = slot_dict.get(slot)
            if tensor is not None:
                # 支持 numpy 数组和 torch 张量
                if isinstance(tensor, np.ndarray):
                    tensor = torch.from_numpy(tensor)
                # 确保是 2D 张量 [batch=1, seq_len]
                if tensor.dim() == 1:
                    tensor = tensor.unsqueeze(0)
                valid_tensors.append(tensor)
            else:
                valid_tensors.append(None)

        # 找到最大长度
        max_len = 1
        for t in valid_tensors:
            if t is not None:
                max_len = max(max_len, t.shape[1])

        # 构建统一长度的张量
        token_ids_list = []
        for t in valid_tensors:
            if t is not None:
                # 填充到最大长度
                seq_len = t.shape[1]
                if seq_len < max_len:
                    padding = torch.full((1, max_len - seq_len), pad_token_id, dtype=torch.long)
                    t = torch.cat([t, padding], dim=1)
                token_ids_list.append(t)
            else:
                # 全 PAD
                token_ids_list.append(torch.full((1, max_len), pad_token_id, dtype=torch.long))

        return torch.cat(token_ids_list, dim=0)  # [6, max_len]

    def encode_batch(
        self,
        slots_list: List[QuerySlots],
        use_raw: bool = True,
        normalize: bool = True,
    ) -> np.ndarray:
        """
        批量编码查询向量

        Args:
            slots_list: 查询槽位数据列表
            use_raw: 是否使用原始嵌入
            normalize: 是否L2归一化

        Returns:
            [batch, 384] 查询向量矩阵
        """
        vectors = []
        for slots in slots_list:
            vec = self.encode(slots, use_raw=use_raw, normalize=normalize)
            vectors.append(vec)
        return np.stack(vectors)

    def get_slot_vectors(
        self,
        slots: QuerySlots,
    ) -> Dict[str, np.ndarray]:
        """
        获取各槽位的独立向量（用于子空间搜索）

        Returns:
            {slot_name: [64] slot_vector}
        """
        self.encoder.eval()
        slot_dict = slots.to_dict()

        with torch.no_grad():
            slot_vectors = {}
            for slot in SLOT_NAMES:
                tensor = slot_dict.get(slot)
                if tensor is not None:
                    # 确保是 2D 张量 [batch=1, seq_len]
                    if tensor.dim() == 1:
                        tensor = tensor.unsqueeze(0)
                    emb = self.encoder.slot_embeddings[slot](tensor)
                    vec = emb.mean(dim=1).squeeze(0).cpu().numpy()  # [64]
                else:
                    # 未知槽位返回零向量
                    vec = np.zeros(SLOT_DIM)
                slot_vectors[slot] = vec

        return slot_vectors


class SlotTokenizer:
    """槽位分词器（简易版本）

    将自然语言文本分词并分配到6个槽位
    实际使用时应该接入真正的 tokenizer（如 BPE）
    """

    def __init__(self, vocab_size: int = 10000):
        self.vocab_size = vocab_size
        # 简易词汇表：实际应从训练好的 tokenizer 加载
        self.word_to_id = {}
        self.id_to_word = {}

    def tokenize(self, text: str) -> List[int]:
        """简单分词（空格分词）"""
        words = text.strip().split()
        ids = []
        for word in words:
            if word not in self.word_to_id:
                if len(self.word_to_id) < self.vocab_size:
                    word_id = len(self.word_to_id)
                    self.word_to_id[word] = word_id
                    self.id_to_word[word_id] = word
                else:
                    # 词汇表满了，返回 UNK
                    word_id = 0
            else:
                word_id = self.word_to_id[word]
            ids.append(word_id)
        return ids

    def encode_slot(self, text: str, slot: str) -> torch.Tensor:
        """编码单个槽位的文本"""
        ids = self.tokenize(text)
        return torch.tensor([ids], dtype=torch.long)

    def encode_slots(
        self,
        scene: Optional[str] = None,
        subject: Optional[str] = None,
        action: Optional[str] = None,
        object: Optional[str] = None,
        purpose: Optional[str] = None,
        result: Optional[str] = None,
    ) -> QuerySlots:
        """编码所有槽位的文本"""
        return QuerySlots(
            scene=self.encode_slot(scene, "scene") if scene else None,
            subject=self.encode_slot(subject, "subject") if subject else None,
            action=self.encode_slot(action, "action") if action else None,
            object=self.encode_slot(object, "object") if object else None,
            purpose=self.encode_slot(purpose, "purpose") if purpose else None,
            result=self.encode_slot(result, "result") if result else None,
        )


# ============================================================
# 便捷函数
# ============================================================

def build_query_vector(
    slots: QuerySlots,
    encoder: Optional[InterestEncoder] = None,
    vocab_size: int = 10000,
    use_raw: bool = True,
    normalize: bool = True,
) -> np.ndarray:
    """
    便捷函数：构建查询向量

    Args:
        slots: 查询槽位数据
        encoder: 已训练的 InterestEncoder（可选）
        vocab_size: 词汇表大小
        use_raw: 是否使用原始嵌入
        normalize: 是否L2归一化

    Returns:
        [384] 查询向量
    """
    pipeline = QueryEncoderPipeline(
        interest_encoder=encoder,
        vocab_size=vocab_size,
    )
    return pipeline.encode(slots, use_raw=use_raw, normalize=normalize)


def parse_and_encode_query(
    query_texts: Dict[str, str],
    encoder: Optional[InterestEncoder] = None,
    vocab_size: int = 10000,
) -> tuple[np.ndarray, Dict[str, np.ndarray]]:
    """
    解析并编码查询

    Args:
        query_texts: {"scene": "...", "subject": "...", ...}
        encoder: 已训练的 InterestEncoder

    Returns:
        (query_vector, slot_vectors)
    """
    tokenizer = SlotTokenizer(vocab_size=vocab_size)
    slots = tokenizer.encode_slots(**query_texts)

    pipeline = QueryEncoderPipeline(
        interest_encoder=encoder,
        vocab_size=vocab_size,
    )

    query_vec = pipeline.encode(slots, use_raw=True, normalize=True)
    slot_vecs = pipeline.get_slot_vectors(slots)

    return query_vec, slot_vecs
