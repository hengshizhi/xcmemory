# 在线学习和数据集管理模块

> 管理人：TODO
> 状态：待实现

## 职责

支持在线学习：LLM 在每次请求记忆时给出更正建议，积累数据后更新兴趣模型。

---

## 核心流程

```
┌─────────────────────────────────────────────────────┐
│                     用户查询                          │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│                   记忆检索返回                        │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│            LLM 判断检索结果是否正确                    │
│                                                       │
│   prompt: "用户问了 X，我返回了 Y，对吗？"              │
│   LLM: "Y不对，应该是 Z，原因：..."                   │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│              积累到更正数据集                          │
│                                                       │
│   (query, returned, correct, reason)                 │
└─────────────────────────────────────────────────────┘
                          ↓
              积累够一批后 → 微调 InterestEncoder
```

---

## API 设计

```python
class OnlineLearning:
    """在线学习和数据集管理"""

    def record_interaction(
        self,
        query: str,  # 用户查询
        returned_memory_id: str,  # 实际返回的记忆
        llm_judgment: LLMJudgment,  # LLM 的判断
    ):
        """记录一次交互到数据集"""

    def get_dataset(self) -> CorrectionDataset:
        """获取积累的更正数据集"""

    def finetune(self, dataset: CorrectionDataset):
        """用更正数据集微调 InterestEncoder"""

    def auto_train_if_ready(self, threshold: int = 100):
        """积累够 threshold 条后自动触发训练"""
```

---

## 数据格式

```python
@dataclass
class CorrectionRecord:
    query: QuerySentence
    returned_memory_id: str
    correct_memory_id: Optional[str]  # LLM 认为正确的记忆ID
    is_correct: bool  # 检索是否正确
    reason: str  # LLM 的理由
    timestamp: datetime
```

---

## LLM 反馈接口

```python
class LLMFeedback:
    """LLM 在返回记忆时给出反馈"""

    def judge(
        self,
        user_query: str,
        returned_memory: Memory,
        context: dict,
    ) -> LLMJudgment:
        """
        prompt:
        用户问: {user_query}
        系统返回了记忆: {returned_memory.content}
        这个记忆是否回答了用户的问题？如果不准确，应该返回哪个记忆？

        返回: {is_correct, correct_memory_id, reason}
        """
```

---

## 训练策略

```
阶段1：预训练（合成数据）
  └─ 学通用维度依赖关系

阶段2：在线微调（用户真实数据）
  └─ 用 CorrectionDataset 微调
  └─ 少量数据 + 对比损失
  └─ RLHF / DPO 可选
```

---

## 待实现

- [ ] LLM 反馈接口设计
- [ ] 数据集持久化
- [ ] 微调策略（full finetune / LoRA）
- [ ] 训练触发条件
- [ ] 避免灾难性遗忘
