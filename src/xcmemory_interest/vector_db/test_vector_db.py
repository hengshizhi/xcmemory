"""测试向量数据库"""
import sys
sys.path.insert(0, 'o:/project/starlate')

import numpy as np
import tempfile
import shutil

from xcmemory_interest.vector_db import ChromaVectorDB, SubspaceSearcher, HybridSearcher

# use temp dir
tmpdir = tempfile.mkdtemp()
print(f'Using temp dir: {tmpdir}')

try:
    # === Test ChromaVectorDB ===
    print()
    print('=== Test ChromaVectorDB ===')
    db = ChromaVectorDB(persist_directory=tmpdir)

    # Test add
    vec1 = np.random.randn(384).astype(np.float32)
    mem_id1 = db.add(vector=vec1, metadata={'subject': 'I', 'action': 'study'})
    print(f'Added: {mem_id1}, count={db.count()}')

    vec2 = np.random.randn(384).astype(np.float32)
    mem_id2 = db.add(vector=vec2, metadata={'subject': 'you', 'action': 'play'})
    print(f'Added: {mem_id2}, count={db.count()}')

    # Test search
    results = db.search(query_vector=vec1, top_k=2)
    print(f'Search results: {len(results)} items')
    for r in results:
        mid = r['memory_id']
        dist = r['distance']
        print(f'  {mid}: distance={dist:.4f}')

    # Test get
    item = db.get(mem_id1, include_vector=True)
    vnorm = np.linalg.norm(item['vector'])
    print(f'Get {mem_id1}: vector norm={vnorm:.4f}')

    # Test exists
    print(f'Exists {mem_id1}: {db.exists(mem_id1)}')
    print(f'Exists nonexistent: {db.exists("mem_xxx")}')

    # Test delete
    db.delete(mem_id2)
    print(f'After delete: count={db.count()}')

    # === Test SubspaceSearcher ===
    print()
    print('=== Test SubspaceSearcher ===')
    db2 = ChromaVectorDB(persist_directory=tmpdir + '_sub')

    for i in range(5):
        vec = np.random.randn(384).astype(np.float32)
        subject = ['a', 'b', 'c', 'd', 'e'][i]
        db2.add(vector=vec, metadata={'subject': subject, 'slot_order': i})

    searcher = SubspaceSearcher(db2)
    # 需要完整的 384 维查询向量
    full_query = np.random.randn(384).astype(np.float32)
    results = searcher.search(
        query_vector=full_query,
        query_slot_vectors={'subject': np.random.randn(64).astype(np.float32)},
        top_k=3,
        rerank=True,
    )
    print(f'Subspace search: {len(results)} items')
    for r in results:
        print(f'  {r["memory_id"]}')

    # === Test HybridSearcher ===
    print()
    print('=== Test HybridSearcher ===')
    db3 = ChromaVectorDB(persist_directory=tmpdir + '_hybrid')

    for i in range(3):
        vec = np.random.randn(384).astype(np.float32)
        db3.add(vector=vec, metadata={'idx': i})

    hsearcher = HybridSearcher(db3)
    results = hsearcher.search(
        query_vector=np.random.randn(384).astype(np.float32),
        top_k=2,
        mode='hybrid',
        keyword_results=['mem_001', 'mem_002'],
        graph_results=['mem_003'],
    )
    print(f'Hybrid search: {len(results)} items')
    for r in results:
        mid = r['memory_id']
        score = r['score']
        print(f'  {mid}: score={score:.4f}')

    print()
    print('=== Test ProbabilitySampler ===')
    from xcmemory_interest.vector_db import ProbabilitySampler, DistanceAwareSampler

    # 创建 20 个随机向量
    n_candidates = 20
    top_k = 20
    n_select = 5

    rng = np.random.default_rng(12345)
    candidates = []
    true_vectors = []
    for i in range(n_candidates):
        vec = rng.standard_normal(384).astype(np.float32)
        true_vectors.append(vec)
        candidates.append({
            "memory_id": f"mem_{i:03d}",
            "vector": vec,
            "metadata": {"index": i},
        })

    # 查询向量（接近第 0 个）
    query = true_vectors[0] + rng.standard_normal(384).astype(np.float32) * 0.5

    # 概率采样（自适应 sigma）
    sampler = ProbabilitySampler(random_seed=42)
    sampled = sampler.sample(candidates, query, top_k=top_k, n_select=n_select)

    print(f"Top-K: {top_k}, Select: {n_select}, Sampled: {len(sampled)}")
    assert len(sampled) == n_select, f"Expected {n_select} samples, got {len(sampled)}"
    print("Sampled memories (ordered by first appearance):")
    for i, s in enumerate(sampled):
        print(f"  {i+1}. {s['memory_id']}, distance={s['distance']:.4f}, prob={s['sample_prob']:.6f}")

    # 验证：mem_000（距离最近）应该有更高的概率
    mem_000 = next((s for s in sampled if s["memory_id"] == "mem_000"), None)
    if mem_000:
        print(f"mem_000 selected (distance={mem_000['distance']:.4f}), prob={mem_000['sample_prob']:.6f}")

    # 距离感知采样
    print()
    print("=== Test DistanceAwareSampler ===")
    dist_sampler = DistanceAwareSampler(distance_threshold=2.0)
    dist_result = dist_sampler.sample(candidates, query, n_select=5)
    print(f"Threshold=2.0, Selected: {[r['memory_id'] for r in dist_result]}")

    print()
    print('=== All tests passed! ===')

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
    shutil.rmtree(tmpdir + '_sub', ignore_errors=True)
    shutil.rmtree(tmpdir + '_hybrid', ignore_errors=True)
