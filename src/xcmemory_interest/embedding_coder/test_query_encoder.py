"""
测试查询词编码模块
"""

import torch
import numpy as np
from xcmemory_interest.embedding_coder import (
    InterestEncoder,
    QueryEncoderPipeline,
    QuerySlots,
    SlotTokenizer,
    build_query_vector,
    parse_and_encode_query,
)


def test_query_slots():
    """测试 QuerySlots 数据类"""
    print("=== 测试 QuerySlots ===")

    slots = QuerySlots(
        time=torch.tensor([[1, 2, 3]]),
        subject=torch.tensor([[4, 5]]),
        action=None,
        object=torch.tensor([[6]]),
        purpose=None,
        result=None,
    )

    print(f"已填充槽位: {slots.get_filled_slots()}")
    print(f"未填充槽位: {slots.get_empty_slots()}")
    print(f"槽位数据: time={slots.time.shape}, subject={slots.subject.shape}")
    print()


def test_query_encoder_pipeline():
    """测试 QueryEncoderPipeline"""
    print("=== 测试 QueryEncoderPipeline ===")

    # 创建 InterestEncoder（模拟已训练的编码器）
    encoder = InterestEncoder(vocab_size=10000, slot_dim=64, num_heads=4, num_layers=2)
    encoder.eval()

    # 测试1: 使用共享嵌入
    print("--- 使用共享 InterestEncoder 嵌入 ---")
    pipeline = QueryEncoderPipeline(interest_encoder=encoder)

    slots = QuerySlots(
        subject=torch.tensor([[1, 2, 3]]),  # "我"
        action=torch.tensor([[4, 5]]),       # "学"
        purpose=torch.tensor([[6, 7, 8]]),   # "喜欢"
    )

    vec = pipeline.encode(slots, use_raw=True, normalize=True)
    print(f"查询向量 shape: {vec.shape}")
    print(f"查询向量范数: {np.linalg.norm(vec):.4f}")

    # 测试2: 获取各槽位向量
    slot_vecs = pipeline.get_slot_vectors(slots)
    print("\n各槽位向量:")
    for slot, vec in slot_vecs.items():
        print(f"  {slot}: {vec.shape}, 范数={np.linalg.norm(vec):.4f}")

    # 测试3: 批量编码
    print("\n--- 批量编码 ---")
    slots_list = [
        QuerySlots(subject=torch.tensor([[1]])),
        QuerySlots(subject=torch.tensor([[2]]), action=torch.tensor([[3]])),
    ]
    batch_vecs = pipeline.encode_batch(slots_list, use_raw=True, normalize=True)
    print(f"批量查询向量 shape: {batch_vecs.shape}")
    print()


def test_slot_tokenizer():
    """测试 SlotTokenizer"""
    print("=== 测试 SlotTokenizer ===")

    tokenizer = SlotTokenizer(vocab_size=1000)

    # 解析结构化查询
    slots = tokenizer.encode_slots(
        subject="我",
        action="学",
        purpose="编程",
    )
    print(f"解析结果: subject={slots.subject}, action={slots.action}, purpose={slots.purpose}")

    # 解析并编码一步到位
    query_vec, slot_vecs = parse_and_encode_query(
        {"subject": "我", "action": "学", "purpose": "编程"},
        vocab_size=1000,
    )
    print(f"查询向量 shape: {query_vec.shape}")
    print(f"查询向量范数: {np.linalg.norm(query_vec):.4f}")
    print()


def test_raw_vs_attention():
    """对比原始嵌入 vs 注意力嵌入"""
    print("=== 对比原始嵌入 vs 注意力嵌入 ===")

    encoder = InterestEncoder(vocab_size=10000, slot_dim=64, num_heads=4, num_layers=2)
    encoder.eval()

    pipeline = QueryEncoderPipeline(interest_encoder=encoder)

    slots = QuerySlots(
        subject=torch.tensor([[1, 2, 3]]),
        action=torch.tensor([[4, 5]]),
    )

    # 原始嵌入
    raw_vec = pipeline.encode(slots, use_raw=True, normalize=True)

    # 注意力嵌入
    attn_vec = pipeline.encode(slots, use_raw=False, normalize=True)

    # 计算相似度
    similarity = np.dot(raw_vec, attn_vec) / (np.linalg.norm(raw_vec) * np.linalg.norm(attn_vec))
    print(f"原始嵌入 vs 注意力嵌入 相似度: {similarity:.4f}")
    print(f"原始嵌入范数: {np.linalg.norm(raw_vec):.4f}")
    print(f"注意力嵌入范数: {np.linalg.norm(attn_vec):.4f}")
    print()


def test_query_vector_consistency():
    """测试查询向量与记忆向量在同一空间"""
    print("=== 测试向量空间一致性 ===")

    encoder = InterestEncoder(vocab_size=10000, slot_dim=64, num_heads=4, num_layers=2)
    encoder.eval()

    pipeline = QueryEncoderPipeline(interest_encoder=encoder)

    # 模拟记忆编码
    memory_ids = torch.randint(0, 1000, (6, 10))  # [6, 10]
    with torch.no_grad():
        memory_vec = encoder.encode_raw(memory_ids).numpy()

    # 模拟查询编码（使用相同的嵌入）
    slots = QuerySlots(
        time=torch.tensor([memory_ids[0]]),
        subject=torch.tensor([memory_ids[1]]),
        action=torch.tensor([memory_ids[2]]),
        object=torch.tensor([memory_ids[3]]),
        purpose=torch.tensor([memory_ids[4]]),
        result=torch.tensor([memory_ids[5]]),
    )
    query_vec = pipeline.encode(slots, use_raw=True, normalize=True)

    # 计算相似度
    similarity = np.dot(memory_vec, query_vec) / (np.linalg.norm(memory_vec) * np.linalg.norm(query_vec))
    print(f"相同内容: 记忆向量 vs 查询向量 相似度: {similarity:.4f}")

    # 不同内容
    diff_slots = QuerySlots(
        time=torch.tensor([[100, 101]]),
        subject=torch.tensor([[200, 201]]),
    )
    diff_vec = pipeline.encode(diff_slots, use_raw=True, normalize=True)
    diff_similarity = np.dot(memory_vec, diff_vec) / (np.linalg.norm(memory_vec) * np.linalg.norm(diff_vec))
    print(f"不同内容: 记忆向量 vs 查询向量 相似度: {diff_similarity:.4f}")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("查询词编码模块测试")
    print("=" * 60)
    print()

    test_query_slots()
    test_query_encoder_pipeline()
    test_slot_tokenizer()
    test_raw_vs_attention()
    test_query_vector_consistency()

    print("=" * 60)
    print("所有测试完成!")
    print("=" * 60)
