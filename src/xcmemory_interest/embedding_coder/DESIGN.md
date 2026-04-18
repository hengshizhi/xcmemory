# 查询句嵌入编码模块

> 管理人：TODO
> 状态：已完成（InterestEncoder + QueryEncoder + QueryEncoderPipeline）

## 已实现

### InterestEncoder

- **文件**: `model.py`
- **功能**: 6槽位查表嵌入 + 多头自注意力 → 384维兴趣嵌入
- **特性**:
  - 支持部分查询（MASK 未知槽位）
  - 自注意力自动学习维度间依赖
  - 参数量: ~12.5M

### QueryEncoder

- **文件**: `model.py`
- **功能**: 6槽位查表嵌入，直接拼接 → 384维原始嵌入
- **用途**: 辅助查询（查某个词在哪些查询句槽位写入过）

### QueryEncoderPipeline

- **文件**: `query_encoder.py`
- **功能**: 查询编码管道，使用原始嵌入作为查询向量
- **特性**:
  - 与 InterestEncoder 共享嵌入表（保证向量空间一致）
  - 支持部分槽位查询
  - 支持 L2 归一化
  - 可获取各槽位独立向量（用于子空间搜索）

### SlotTokenizer

- **文件**: `query_encoder.py`
- **功能**: 槽位分词器（简易版）
- **用途**: 将自然语言文本分词并分配到6个槽位

### QuerySlots

- **文件**: `query_encoder.py`
- **功能**: 查询槽位数据类
- **特性**:
  - 支持 6 个槽位的独立张量存储
  - 提供 `get_filled_slots()` / `get_empty_slots()` 方法

## 向量空间设计

```
记忆编码: InterestEncoder.encode_raw(token_ids) → [384]
查询编码: QueryEncoderPipeline.encode(slots) → [384]

两者共享同一个嵌入表，生成的向量在同一空间，可直接用于相似度计算。
```

## API

```python
# === 基础编码 ===
interest_vec = encoder.encode_memory_with_ids(token_ids)  # [384]
query_vec = encoder.encode_query_with_ids(time=..., action=...)  # [384]
raw_vec = encoder.encode_raw(token_ids)  # [384]

# === 查询编码管道（推荐）===
pipeline = QueryEncoderPipeline(interest_encoder=encoder)
slots = QuerySlots(subject=torch.tensor([[1, 2]]), action=torch.tensor([[3]]))
query_vec = pipeline.encode(slots, use_raw=True, normalize=True)
slot_vecs = pipeline.get_slot_vectors(slots)  # {slot: [64]}

# === 便捷函数 ===
query_vec = build_query_vector(slots, encoder=encoder)
query_vec, slot_vecs = parse_and_encode_query({"subject": "我", "action": "学"})

# === 槽位分词 ===
tokenizer = SlotTokenizer(vocab_size=10000)
slots = tokenizer.encode_slots(subject="我", action="学", purpose="编程")
```

## 待实现

- [ ] 接入真正的 tokenizer（如 BPE）
- [ ] 训练脚本
- [ ] 模型保存/加载
