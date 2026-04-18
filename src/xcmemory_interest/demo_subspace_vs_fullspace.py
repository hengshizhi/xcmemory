# -*- coding: utf-8 -*-
"""
星尘记忆系统 - 子空间 vs 全空间搜索对比演示（VecDBCRUD 版）

对比：
- 子空间搜索：每个槽位独立 64 维 Collection 精准查找 + 交集
- 全空间搜索：384 维完整向量 Collection 搜索
"""

import sys
import os
import shutil

sys.path.insert(0, 'O:/project')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xcmemory_interest.basic_crud import VecDBCRUD, EmbeddingMode


def main():
    test_dir = os.path.join(os.path.dirname(__file__), 'test_demo')

    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)

    print('=' * 70)
    print('VecDBCRUD 子空间搜索 vs 全空间搜索 对比演示')
    print('  子空间：6 个独立 64 维 Collection → 各自搜索 → 取交集')
    print('  全空间：1 个 384 维 Collection → 完整向量搜索')
    print('=' * 70)

    # 1. 初始化
    db = VecDBCRUD(persist_directory=test_dir)

    # 2. 写入测试记忆
    print('\n[1] 写入测试记忆')
    memories = [
        ('<平时><我><学><编程><喜欢><有收获>', '我平时喜欢学编程'),
        ('<周末><我><打><篮球><锻炼身体><很爽>', '周末打篮球'),
        ('<晚上><我><看><书><学习知识><进步>', '晚上看书学习'),
        ('<假期><我><去><旅行><放松心情><开心>', '假期去旅行'),
        ('<平时><我><写><代码><工作需要><完成任务>', '平时写代码工作'),
        ('<周末><我><做饭><美食><享受生活><满足>', '周末做饭享受生活'),
        ('<晚上><我><散步><公园><锻炼身体><健康>', '晚上散步公园'),
        ('<假期><朋友><约><我><一起玩><很开心>', '朋友约我玩'),
    ]

    for query, content in memories:
        db.write(query, content, lifecycle=30, embedding_mode=EmbeddingMode.INTEREST)

    print(f'    共写入 {db.count()} 条记忆')
    print(f'    各槽位 Collection 向量数: {db.slot_counts}')
    print(f'    全量 Collection 向量数: {db.full_count}')

    # 3. 子空间搜索（单槽位）
    print('\n[2] 子空间搜索 - 单槽位 (subject=我, use_slot_rerank=True)')
    subspace_results = db.search_subspace(
        query_slots={'subject': '我'},
        top_k=5,
        use_slot_rerank=True,
    )
    print(f'    查询: subject=我')
    print(f'    结果数: {len(subspace_results)}')
    for i, r in enumerate(subspace_results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs} (匹配={r.match_count}, 距离={r.avg_distance:.4f}, sort_by={r.sort_by})')

    # 3.1 子空间搜索（不排序）
    print('\n[2.1] 子空间搜索 - 单槽位 (subject=我, use_slot_rerank=False)')
    subspace_results_no_rerank = db.search_subspace(
        query_slots={'subject': '我'},
        top_k=5,
        use_slot_rerank=False,
    )
    for i, r in enumerate(subspace_results_no_rerank):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs} (dist={r.distance:.4f}, sort_by={r.sort_by})')

    # 4. 全空间搜索
    print('\n[3] 全空间搜索 (subject=我, use_slot_rerank=True)')
    fullspace_results = db.search_fullspace(
        query_slots={'subject': '我'},
        top_k=5,
        embedding_mode=EmbeddingMode.INTEREST,
        use_slot_rerank=True,
    )
    for i, r in enumerate(fullspace_results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs} (匹配={r.match_count}, dist={r.distance:.4f}, sort_by={r.sort_by})')

    # 5. 多槽位子空间搜索
    print('\n[4] 多槽位子空间搜索 (subject=我, purpose=锻炼身体)')
    sub_results = db.search_subspace(
        query_slots={'subject': '我', 'purpose': '锻炼身体'},
        top_k=5,
        use_slot_rerank=True,
    )
    print(f'    结果数: {len(sub_results)}')
    for i, r in enumerate(sub_results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs} (匹配={r.match_count}, 距离={r.avg_distance:.4f})')

    # 5.1 多槽位全空间搜索
    print('\n[4.1] 多槽位全空间搜索 (subject=我, purpose=锻炼身体)')
    full_results = db.search_fullspace(
        query_slots={'subject': '我', 'purpose': '锻炼身体'},
        top_k=5,
        embedding_mode=EmbeddingMode.INTEREST,
        use_slot_rerank=True,
    )
    for i, r in enumerate(full_results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs} (匹配={r.match_count}, dist={r.distance:.4f})')

    # 6. CRUD 验证
    print('\n[5] CRUD 验证')
    # 读取
    first_id = sub_results[0].memory_id if sub_results else None
    if first_id:
        mem = db.read(first_id)
        print(f'    read({first_id}) → content="{mem.content}"' if mem else f'    read({first_id}) → None')
        # 更新
        db.update(first_id, content="更新后的内容", lifecycle=15)
        mem2 = db.read(first_id)
        print(f'    update → content="{mem2.content}", lifecycle={mem2.lifecycle}')
        # 删除
        db.delete(first_id)
        mem3 = db.read(first_id)
        print(f'    delete → read={mem3}')
        print(f'    剩余记忆: {db.count()}')

    # 清理
    db.close()
    shutil.rmtree(test_dir, ignore_errors=True)

    print('\n' + '=' * 70)
    print('演示完成！')
    print('=' * 70)


if __name__ == '__main__':
    main()
