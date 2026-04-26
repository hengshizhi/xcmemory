"""
星尘记忆 - 兴趣编码器
Slot Self-Attention Encoder for Structured Memory

6个可学习槽位嵌入 + 自注意力 = 维度关系学习
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, List, Tuple, Dict

# 槽位定义
SLOT_NAMES = ["scene", "subject", "action", "object", "purpose", "result"]
NUM_SLOTS = len(SLOT_NAMES)
SLOT_DIM = 64  # 每个槽位64维


class InterestEncoder(nn.Module):
    """兴趣编码器
    - 6个可学习嵌入向量（查表得到初始表示）
    - 自注意力让维度间自主学习依赖关系
    - 支持部分查询（MASK 未知槽位）
    """

    def __init__(
        self,
        vocab_size: int = 10000,  # 词汇表大小（用于槽位内容的离散编码）
        slot_dim: int = SLOT_DIM,
        num_heads: int = 4,
        num_layers: int = 2,
        dropout: float = 0.0,
    ):
        super().__init__()
        self.slot_dim = slot_dim
        self.num_slots = NUM_SLOTS
        self.vocab_size = vocab_size

        # 6个槽位各一个可学习嵌入向量（查表得到槽位内容向量）
        self.slot_embeddings = nn.ModuleDict({
            slot: nn.Embedding(vocab_size, slot_dim)
            for slot in SLOT_NAMES
        })

        # 可学习的 [MASK] 向量（用于未知槽位）
        self.mask_vector = nn.Parameter(torch.randn(1, slot_dim))

        # 可学习的 [CLS] 向量（聚合全局信息）
        self.cls_vector = nn.Parameter(torch.randn(1, slot_dim))

        # 自注意力层
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=slot_dim,
            nhead=num_heads,
            dim_feedforward=slot_dim * 4,
            activation=F.silu,
            batch_first=True,
            dropout=dropout,
            norm_first=True,  # Pre-Norm 更稳定
        )
        self.attn_layers = nn.TransformerEncoder(
            encoder_layer,
            num_layers=num_layers,
        )

        # 输出投影
        self.out_proj = nn.Linear(slot_dim, slot_dim)

        # 初始化
        self._init_weights()

    def _init_weights(self):
        for emb in self.slot_embeddings.values():
            nn.init.normal_(emb.weight, std=0.02)
        nn.init.normal_(self.mask_vector, std=0.02)
        nn.init.normal_(self.cls_vector, std=0.02)

    def encode_slot(self, slot: str, token_ids: torch.Tensor) -> torch.Tensor:
        """将 token_ids 序列编码为单个槽位向量
        Args:
            slot: 槽位名称
            token_ids: [batch, seq_len] token id 序列
        Returns:
            [batch, slot_dim] 槽位向量
        """
        # 查嵌入表
        embeddings = self.slot_embeddings[slot](token_ids)  # [batch, seq_len, slot_dim]
        # 平均池化
        return embeddings.mean(dim=1)  # [batch, slot_dim]

    def encode_memory(self, parts: List[str], token_ids: torch.Tensor) -> torch.Tensor:
        """编码完整记忆
        Args:
            parts: ["平时", "我", "做", "实验", "为了", "学习进步"]
            token_ids: [6, seq_len] 6个槽位各自的 token_ids
        Returns:
            [384] = 6 * 64，拼接所有槽位向量
        """
        assert len(parts) == self.num_slots, f"期望{self.num_slots}个槽位，实际{len(parts)}"

        # 构建序列: [7, 64] = [CLS] + 6个槽位
        cls = self.cls_vector.expand(1, -1)  # [1, 64]

        # 6个槽位向量
        slot_embs = []
        for i, slot in enumerate(SLOT_NAMES):
            emb = self.slot_embeddings[slot](token_ids[i:i+1])  # [1, seq_len, 64]
            emb = emb.mean(dim=1)  # [1, 64]
            slot_embs.append(emb)

        seq = torch.stack(slot_embs, dim=0).squeeze(1)  # [6, 64]

        # 加入 [CLS]
        full_seq = torch.cat([cls, seq], dim=0)  # [7, 64]

        # 自注意力
        output = self.attn_layers(full_seq)  # [7, 64]

        # 去掉 [CLS]，取6个槽位输出
        slot_outputs = output[1:, :]  # [6, 64]

        return slot_outputs.flatten()  # [384]

    def encode_query(
        self,
        scene: Optional[torch.Tensor] = None,
        subject: Optional[torch.Tensor] = None,
        action: Optional[torch.Tensor] = None,
        object: Optional[torch.Tensor] = None,
        purpose: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """编码部分查询
        Args:
            scene, subject, action, object, purpose: 各槽位的 token_ids，未知为 None
        Returns:
            [384] 查询向量
        """
        slot_values = {
            "scene": scene,
            "subject": subject,
            "action": action,
            "object": object,
            "purpose": purpose,
            "result": None,  # 查询时不知道结果
        }

        # 构建序列
        cls = self.cls_vector.expand(1, -1)  # [1, 64]

        slot_embs = []
        for slot in SLOT_NAMES:
            val = slot_values[slot]
            if val is not None:
                # 已知槽位：编码
                emb = self.slot_embeddings[slot](val)  # [1, seq_len, 64]
                emb = emb.mean(dim=1)  # [1, 64]
            else:
                # 未知槽位：[MASK]
                emb = self.mask_vector  # [1, 64]
            slot_embs.append(emb)

        seq = torch.stack(slot_embs, dim=0).squeeze(1)  # [6, 64]
        full_seq = torch.cat([cls, seq], dim=0)  # [7, 64]

        # 自注意力
        output = self.attn_layers(full_seq)  # [7, 64]

        # 去掉 [CLS]
        slot_outputs = output[1:, :]  # [6, 64]

        return slot_outputs.flatten()  # [384]

    def forward(
        self,
        memory_ids: torch.Tensor,  # [6, mem_seq_len] 记忆的token_ids
        query_ids: dict,  # 各槽位的query token_ids
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """前向
        Returns:
            (memory_vec, query_vec) 各384维
        """
        mem_vec = self.encode_memory_with_ids(memory_ids)
        query_vec = self.encode_query_with_ids(**query_ids)
        return mem_vec, query_vec

    def encode_memory_with_ids(self, token_ids: torch.Tensor) -> torch.Tensor:
        """用 token_ids 编码记忆
        Args:
            token_ids: [6, seq_len]
        """
        cls = self.cls_vector.expand(1, -1)
        slot_embs = [
            self.slot_embeddings[slot](token_ids[i:i+1]).mean(dim=1)
            for i, slot in enumerate(SLOT_NAMES)
        ]
        seq = torch.stack(slot_embs, dim=0).squeeze(1)
        full_seq = torch.cat([cls, seq], dim=0)
        output = self.attn_layers(full_seq)
        return output[1:, :].flatten()

    def encode_query_with_ids(
        self,
        scene: Optional[torch.Tensor] = None,
        subject: Optional[torch.Tensor] = None,
        action: Optional[torch.Tensor] = None,
        object: Optional[torch.Tensor] = None,
        purpose: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """用 token_ids 编码查询"""
        slot_values = {
            "scene": scene, "subject": subject, "action": action,
            "object": object, "purpose": purpose,
        }

        cls = self.cls_vector.expand(1, -1)
        slot_embs = []
        for slot in SLOT_NAMES:
            val = slot_values.get(slot)
            if val is not None:
                emb = self.slot_embeddings[slot](val).mean(dim=1)
            else:
                emb = self.mask_vector
            slot_embs.append(emb)

        seq = torch.stack(slot_embs, dim=0).squeeze(1)
        full_seq = torch.cat([cls, seq], dim=0)
        output = self.attn_layers(full_seq)
        return output[1:, :].flatten()

    def encode_query_with_ids_slots(
        self,
        scene: Optional[torch.Tensor] = None,
        subject: Optional[torch.Tensor] = None,
        action: Optional[torch.Tensor] = None,
        object: Optional[torch.Tensor] = None,
        purpose: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        """
        编码查询，同时返回各槽位经过自注意力处理后的向量。

        Returns:
            (full_vector [384], slot_vectors Dict[slot_name, slot_vector [64]])
        """
        slot_values = {
            "scene": scene, "subject": subject, "action": action,
            "object": object, "purpose": purpose,
        }

        cls = self.cls_vector.expand(1, -1)
        slot_embs = []
        for slot in SLOT_NAMES:
            val = slot_values.get(slot)
            if val is not None:
                emb = self.slot_embeddings[slot](val).mean(dim=1)
            else:
                emb = self.mask_vector
            slot_embs.append(emb)

        seq = torch.stack(slot_embs, dim=0).squeeze(1)  # [6, 64]
        full_seq = torch.cat([cls, seq], dim=0)          # [7, 64]
        output = self.attn_layers(full_seq)              # [7, 64]

        slot_outputs = output[1:, :]                      # [6, 64]
        full_vector = slot_outputs.flatten()             # [384]

        # 构建槽位字典
        slot_dict = {}
        for i, slot in enumerate(SLOT_NAMES):
            slot_dict[slot] = slot_outputs[i]

        return full_vector, slot_dict

    def encode_raw(self, token_ids: torch.Tensor) -> torch.Tensor:
        """原始嵌入：不经过自注意力，直接拼接槽位向量
        用于辅助查询（查某个词在哪些查询句槽位写入过）
        Returns:
            [384] 原始拼接向量
        """
        slot_embs = [
            self.slot_embeddings[slot](token_ids[i:i+1]).mean(dim=1)
            for i, slot in enumerate(SLOT_NAMES)
        ]
        seq = torch.stack(slot_embs, dim=0).squeeze(1)
        return seq.flatten()  # [384]


class QueryEncoder(nn.Module):
    """查询句原始嵌入编码器
    不经过自注意力，直接对6个槽位查表拼接
    用于辅助查询：查某个词在哪些查询句槽位写入过
    """

    def __init__(self, vocab_size: int = 10000, slot_dim: int = SLOT_DIM):
        super().__init__()
        self.slot_dim = slot_dim
        self.slot_embeddings = nn.ModuleDict({
            slot: nn.Embedding(vocab_size, slot_dim)
            for slot in SLOT_NAMES
        })
        self._init_weights()

    def _init_weights(self):
        for emb in self.slot_embeddings.values():
            nn.init.normal_(emb.weight, std=0.02)

    def encode(
        self,
        scene: Optional[torch.Tensor] = None,
        subject: Optional[torch.Tensor] = None,
        action: Optional[torch.Tensor] = None,
        object: Optional[torch.Tensor] = None,
        purpose: Optional[torch.Tensor] = None,
        result: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """编码查询句（原始嵌入，不过自注意力）"""
        slot_values = {
            "scene": scene, "subject": subject, "action": action,
            "object": object, "purpose": purpose, "result": result,
        }

        vectors = []
        for slot in SLOT_NAMES:
            val = slot_values.get(slot)
            if val is not None:
                emb = self.slot_embeddings[slot](val).mean(dim=1)
            else:
                emb = torch.zeros(1, self.slot_dim)
            vectors.append(emb)

        seq = torch.stack(vectors, dim=0).squeeze(1)  # [6, 64]
        return seq.flatten()  # [384]


class ContrastiveLoss(nn.Module):
    """对比损失：正样本相似度高，负样本相似度低"""

    def __init__(self, margin: float = 0.3):
        super().__init__()
        self.margin = margin

    def forward(
        self,
        pos_mem_vec: torch.Tensor,
        query_vec: torch.Tensor,
        neg_mem_vecs: List[torch.Tensor],
    ) -> torch.Tensor:
        """
        Args:
            pos_mem_vec: [384] 正样本记忆
            query_vec: [384] 查询
            neg_mem_vecs: 多条负样本记忆
        """
        pos_sim = F.cosine_similarity(
            pos_mem_vec.unsqueeze(0), query_vec.unsqueeze(0)
        ).squeeze()

        neg_sims = []
        for neg in neg_mem_vecs:
            sim = F.cosine_similarity(
                neg.unsqueeze(0), query_vec.unsqueeze(0)
            )
            neg_sims.append(sim)
        neg_sim = torch.stack(neg_sims).mean()

        loss = F.relu(neg_sim - pos_sim + self.margin)
        return loss
