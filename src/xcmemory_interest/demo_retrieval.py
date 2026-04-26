"""
星尘记忆系统 - 检索演示

流程：
  查询词 → 编码向量 → Chroma检索 → memory_id列表 → KV数据库获取内容（未实现）

向量数据库只负责：
  memory_id → 向量（用于向量检索）
  metadata → 槽位字符串（用于参考）

KV数据库（未实现）负责：
  memory_id → Memory{query_sentence, content, lifecycle...}
"""

import sys
import os
import tempfile
import numpy as np
import torch

sys.path.insert(0, 'O:/project')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xcmemory_interest.embedding_coder import (
    InterestEncoder,
    QueryEncoderPipeline,
    QuerySlots,
)
from xcmemory_interest.vector_db import ChromaVectorDB, ProbabilitySampler

print("=" * 60)
print("星尘记忆系统 - 检索演示")
print("=" * 60)

# ============================================================
# 1. 初始化 InterestEncoder
# ============================================================
print("\n" + "=" * 60)
print("1. 初始化 InterestEncoder")
print("=" * 60)

encoder = InterestEncoder(
    vocab_size=10000,
    slot_dim=64,
    num_heads=4,
    num_layers=2,
)
encoder.eval()

print(f"模型参数量: {sum(p.numel() for p in encoder.parameters()):,}")
print(f"向量维度: 6槽 × 64维 = 384维")

# ============================================================
# 2. 准备查询数据
# ============================================================
print("\n" + "=" * 60)
print("2. 准备查询数据")
print("=" * 60)

# 槽位名称
SLOT_NAMES = ["scene", "subject", "action", "object", "purpose", "result"]


def text_to_slot_ids(texts: dict) -> QuerySlots:
    """将文本槽位转换为 token ids"""
    result = {}
    for slot in SLOT_NAMES:
        text = texts.get(slot, "")
        if text:
            # 简单分词：用字符编码 + 位置编码
            token_ids = [ord(c) % 100 for c in text]
        else:
            token_ids = []
        result[slot] = torch.tensor([[i + 1 for i in token_ids]]) if token_ids else None
    return QuerySlots(**result)


def slots_to_tensor(**kwargs) -> QuerySlots:
    """将文本参数转换为 QuerySlots"""
    result = {}
    for slot in SLOT_NAMES:
        text = kwargs.get(slot, "")
        if text:
            token_ids = [ord(c) % 100 for c in text]
            result[slot] = torch.tensor([[i + 1 for i in token_ids]])
        else:
            result[slot] = None
    return QuerySlots(**result)


# 3条查询
queries = [
    {"name": "查询1: 我喜欢学什么？", "slots": {"subject": "我", "action": "学", "purpose": "喜欢"}},
    {"name": "查询2: 周末做什么？", "slots": {"scene": "周末"}},
    {"name": "查询3: 怎么保持健康？", "slots": {"purpose": "健康"}},
]

print("\n查询列表:")
for i, q in enumerate(queries):
    print(f"  [{i}] {q['name']}")
    for slot, value in q["slots"].items():
        print(f"      {slot}={value}")

# ============================================================
# 3. 初始化向量数据库
# ============================================================
print("\n" + "=" * 60)
print("3. 初始化向量数据库")
print("=" * 60)

temp_dir = tempfile.mkdtemp(prefix="xcmemory_demo_")
db = ChromaVectorDB(persist_directory=temp_dir)
print(f"使用临时目录: {temp_dir}")

# ============================================================
# 4. 添加一些"记忆"（实际上只存向量，不存内容）
# ============================================================
print("\n" + "=" * 60)
print("4. 存入向量数据（只存向量+metadata，内容在KV数据库）")
print("=" * 60)

# 模拟一些记忆的元数据（实际内容存在KV数据库）
memories = [
    {"id": "mem_001", "scene": "平时", "subject": "我", "action": "学", "object": "编程", "purpose": "喜欢", "result": "有收获"},
    {"id": "mem_002", "scene": "周末", "subject": "我", "action": "打", "object": "篮球", "purpose": "锻炼身体", "result": "很爽"},
    {"id": "mem_003", "scene": "晚上", "subject": "我", "action": "看", "object": "书", "purpose": "学习知识", "result": "进步"},
    {"id": "mem_004", "scene": "假期", "subject": "我", "action": "去", "object": "旅行", "purpose": "放松心情", "result": "开心"},
    {"id": "mem_005", "scene": "平时", "subject": "我", "action": "写", "object": "代码", "purpose": "工作需要", "result": "完成任务"},
    {"id": "mem_006", "scene": "周末", "subject": "我", "action": "做饭", "object": "美食", "purpose": "享受生活", "result": "满足"},
    {"id": "mem_007", "scene": "晚上", "subject": "我", "action": "散步", "object": "公园", "purpose": "锻炼身体", "result": "健康"},
    {"id": "mem_008", "scene": "假期", "subject": "朋友", "action": "约", "object": "我", "purpose": "一起玩", "result": "很开心"},
]

# 编码并存储
for mem in memories:
    slots = slots_to_tensor(**{k: v for k, v in mem.items() if k != "id"})
    # 用 InterestEncoder 编码
    pipeline = QueryEncoderPipeline(interest_encoder=encoder)
    vector = pipeline.encode(slots, normalize=True)

    # 存入向量数据库（只存向量+metadata，内容在KV数据库）
    db.add(
        memory_id=mem["id"],
        vector=vector,
        metadata={
            "scene": mem["scene"],
            "subject": mem["subject"],
            "action": mem["action"],
            "object": mem["object"],
            "purpose": mem["purpose"],
            "result": mem["result"],
        },
    )
    print(f"  Added: {mem['id']}")

print(f"\n向量数据库当前共 {db.count()} 条向量")
print("  (注意：记忆内容在 KV 数据库，本演示不涉及)")

# ============================================================
# 5. 检索示例
# ============================================================
print("\n" + "=" * 60)
print("5. 检索示例")
print("=" * 60)

pipeline = QueryEncoderPipeline(interest_encoder=encoder)

for q in queries:
    print(f"\n[{q['name']}]")
    print(f"  槽位: {q['slots']}")

    # 编码查询
    query_slots = slots_to_tensor(**q["slots"])
    query_vector = pipeline.encode(query_slots, normalize=True)

    # Chroma 搜索
    print(f"\n  [Chroma 搜索 top_k=3]")
    results = db.search(query_vector=query_vector, top_k=3)
    for r in results:
        m = r["metadata"]
        print(f"    - <{m['scene']}><{m['subject']}><{m['action']}><{m['object']}><{m['purpose']}><{m['result']}> (distance: {r['distance']:.4f})")

    # 概率采样器
    print(f"\n  [概率采样器: top_k=8 -> 采样 3]")
    sampler = ProbabilitySampler(random_seed=42)
    sampled = sampler.sample(
        candidates=db.search(query_vector=query_vector, top_k=8),
        query_vector=query_vector,
        top_k=8,
        n_select=3,
    )
    for s in sampled:
        m = s["metadata"]
        print(f"    - <{m['scene']}><{m['subject']}><{m['action']}><{m['object']}><{m['purpose']}><{m['result']}> (prob: {s['sample_prob']:.4f})")

# ============================================================
# 6. 向量空间一致性验证
# ============================================================
print("\n" + "=" * 60)
print("6. 向量空间一致性验证")
print("=" * 60)

# 同一内容编码后应该高度相似
slots1 = slots_to_tensor(scene="平时", subject="我", action="学", object="编程", purpose="喜欢", result="有收获")
vec1 = pipeline.encode(slots1, normalize=True)

slots2 = slots_to_tensor(scene="平时", subject="我", action="学", object="编程", purpose="喜欢", result="有收获")
vec2 = pipeline.encode(slots2, normalize=True)

slots3 = slots_to_tensor(scene="周末", subject="我", action="打", object="篮球", purpose="锻炼身体", result="很爽")
vec3 = pipeline.encode(slots3, normalize=True)

# 计算相似度
sim_same = np.dot(vec1, vec2)  # 相同内容
sim_diff = np.dot(vec1, vec3)  # 不同内容

print(f"  相同内容相似度: {sim_same:.4f} (应该 ≈ 1.0)")
print(f"  不同内容相似度: {sim_diff:.4f} (应该较低)")

# ============================================================
# 7. 清理
# ============================================================
print("\n" + "=" * 60)
print("7. 清理")
print("=" * 60)
db.close()
print(f"临时目录: {temp_dir}")
print("(Windows 文件锁可能导致目录不完全清理，无需在意)")

print("\n" + "=" * 60)
print("演示完成！")
print("=" * 60)
