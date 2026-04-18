"""
GraphQuery 测试演示

测试记忆图查询功能：
- MemoryGraph: 基础图查询
- GraphExplorer: 高级探索功能
"""

import sys
import os

# 添加项目根目录到 Python 路径 (使用 raw string 避免转义问题)
_project_root = r"o:\project\starlate"
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from models.xcmemory_interest.basic_crud.vec_db_crud import VecDBCRUD
from models.xcmemory_interest.graph_query import MemoryGraph, GraphExplorer, GraphSearchResult


def create_test_data(vec_db: VecDBCRUD):
    """创建测试记忆数据"""
    # 清空已有数据
    vec_db.clear()

    # 写入测试记忆
    memories = [
        ("<平时><我><学习><Python><提升><技能>",
         "我平时通过看书和视频学习Python编程，主要是为了提升技能",
         5),
        ("<最近><我><学习><Go><掌握><并发>",
         "最近在学习Go语言，主要是为了掌握并发编程能力",
         3),
        ("<周末><我><跑步><公园><锻炼><身体>",
         "周末经常去公园跑步锻炼身体，保持健康",
         4),
        ("<平时><我><跑步><操场><保持><体能>",
         "我平时在操场跑步来保持体能",
         3),
        ("<最近><我><学习><Go><深入><微服务>",
         "最近在深入学习Go语言微服务开发",
         2),
        ("<去年><我><学习><Python><完成><项目>",
         "去年我学习了Python并完成了一个项目",
         1),
        ("<平时><朋友><讨论><技术><分享><经验>",
         "我平时和朋友讨论技术，分享经验",
         4),
        ("<最近><同事><学习><Python><合作><开发>",
         "我的同事最近在学习Python，我们一起合作开发",
         3),
    ]

    ids = []
    for query, content, lifecycle in memories:
        mid = vec_db.write(query, content, lifecycle)
        ids.append(mid)
        print(f"写入: {mid} -> {query[:30]}...")

    return ids


def test_memory_graph(vec_db: VecDBCRUD, memory_ids):
    """测试 MemoryGraph 基础功能"""
    print("\n" + "="*60)
    print("测试 MemoryGraph 基础功能")
    print("="*60)

    graph = MemoryGraph(vec_db)

    # 1. 测试 get_neighbors
    print("\n[1] get_neighbors - 获取相邻节点")
    test_id = memory_ids[0]
    print(f"以记忆 {test_id} 为起点查找邻居:")
    neighbors = graph.get_neighbors(test_id, min_shared_slots=1)
    for n in neighbors[:5]:
        print(f"  -> {n.memory_id} (共享槽位: {n.shared_slots})")

    # 2. 测试 get_connected_component
    print("\n[2] get_connected_component - 获取连通分量")
    component = graph.get_connected_component(test_id, min_shared_slots=1)
    print(f"与 {test_id} 连通的记忆共 {len(component)} 个:")
    for c in component[:5]:
        print(f"  {c.memory_id} (距离: {c.distance}, 路径: {c.path})")

    # 3. 测试 find_path
    print("\n[3] find_path - 查找路径")
    if len(memory_ids) >= 3:
        path = graph.find_path(memory_ids[0], memory_ids[4], max_depth=3)
        print(f"从 {memory_ids[0]} 到 {memory_ids[4]}:")
        if path:
            print(f"  路径: {' -> '.join(path)}")
        else:
            print("  未找到路径")

    # 4. 测试 get_connection_strength
    print("\n[4] get_connection_strength - 计算连接强度")
    if len(memory_ids) >= 2:
        strength = graph.get_connection_strength(memory_ids[0], memory_ids[1])
        print(f"记忆 {memory_ids[0]} 与 {memory_ids[1]} 的连接强度:")
        print(f"  共享槽位: {strength['shared_slots']}")
        print(f"  强度值: {strength['strength']:.2f}")
        print(f"  是否连接: {strength['is_connected']}")

    # 5. 测试 find_memories_by_value_chain
    print("\n[5] find_memories_by_value_chain - 沿槽位值链搜索")
    chain_results = graph.find_memories_by_value_chain(
        memory_ids[0],
        value_slots=["action", "object"],
        max_depth=3
    )
    print(f"从 '{memory_ids[0]}' 出发沿 action->object 槽位链查找:")
    for r in chain_results[:5]:
        print(f"  -> {r.memory_id} (共享: {r.shared_slots})")


def test_graph_explorer(vec_db: VecDBCRUD, memory_ids):
    """测试 GraphExplorer 高级功能"""
    print("\n" + "="*60)
    print("测试 GraphExplorer 高级功能")
    print("="*60)

    explorer = GraphExplorer(vec_db)

    # 1. 测试 find_similar_memories
    print("\n[1] find_similar_memories - 查找相似记忆")
    test_id = memory_ids[0]
    similar = explorer.find_similar_memories(test_id, top_k=5, min_shared_slots=1)
    print(f"与 {test_id} 相似的记忆:")
    for s in similar:
        print(f"  -> {s.memory_id} (共享槽位: {list(s.shared_slots.keys())})")

    # 2. 测试 find_memory_clusters
    print("\n[2] find_memory_clusters - 发现记忆簇")
    clusters = explorer.find_memory_clusters(min_cluster_size=2, min_shared_slots=1)
    print(f"发现 {len(clusters)} 个记忆簇:")
    for cluster in clusters:
        print(f"  簇 {cluster.cluster_id}: {cluster.size} 个记忆, 平均生命周期: {cluster.avg_lifecycle:.1f}")
        if cluster.shared_patterns:
            print(f"    共享模式: {dict(list(cluster.shared_patterns.items())[:2])}")

    # 3. 测试 trace_memory_evolution
    print("\n[3] trace_memory_evolution - 追溯记忆进化链")
    evolution = explorer.trace_memory_evolution(memory_ids[0], max_depth=2)
    print(f"记忆 {memory_ids[0]} 的进化链:")
    for e in evolution[:5]:
        print(f"  距离 {e.distance}: {e.memory_id}")

    # 4. 测试 find_bridging_memories
    print("\n[4] find_bridging_memories - 查找桥梁记忆")
    if len(memory_ids) >= 4:
        bridging = explorer.find_bridging_memories(memory_ids[0], memory_ids[3], max_depth=3)
        print(f"连接 {memory_ids[0]} 和 {memory_ids[3]} 的桥梁:")
        if bridging:
            print(f"  路径: {' -> '.join(bridging)}")
        else:
            print("  未找到桥梁")

    # 5. 测试 get_memory_importance
    print("\n[5] get_memory_importance - 计算记忆重要性")
    importance = explorer.get_memory_importance(memory_id=memory_ids[0])
    print(f"记忆 {memory_ids[0]} 的重要性分数: {importance.get(memory_ids[0], 0):.1f}")

    # 6. 测试 find_hub_memories
    print("\n[6] find_hub_memories - 查找枢纽记忆")
    hubs = explorer.find_hub_memories(top_k=3)
    print(f"Top 3 枢纽记忆:")
    for hub_id, count in hubs:
        print(f"  {hub_id}: {count} 个连接")

    # 7. 测试 batch_find_similar
    print("\n[7] batch_find_similar - 批量查找相似记忆")
    batch_ids = memory_ids[:3]
    batch_results = explorer.batch_find_similar(batch_ids, top_k_per=3)
    print(f"批量查找 {len(batch_ids)} 条记忆的相似记忆:")
    for bid, results in batch_results.items():
        print(f"  {bid}: {len(results)} 个相似记忆")


def main():
    print("GraphQuery 功能测试")
    print("="*60)

    # 初始化 VecDBCRUD
    db_path = os.path.join(os.path.dirname(__file__), "vec_db_test_data", "graph_test")
    vec_db = VecDBCRUD(persist_directory=db_path)

    try:
        # 创建测试数据
        memory_ids = create_test_data(vec_db)

        if len(memory_ids) < 2:
            print("需要至少2条记忆才能测试")
            return

        # 测试 MemoryGraph
        test_memory_graph(vec_db, memory_ids)

        # 测试 GraphExplorer
        test_graph_explorer(vec_db, memory_ids)

        print("\n" + "="*60)
        print("测试完成!")
        print("="*60)

    finally:
        vec_db.close()


if __name__ == "__main__":
    main()
