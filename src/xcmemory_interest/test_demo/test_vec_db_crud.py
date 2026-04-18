# -*- coding: utf-8 -*-
"""
VecDBCRUD 完整演示 - 子空间精准查找

架构：
  6 个独立 64 维 Collection → 各自搜索 → 取交集 → 排序
  1 个全量 384 维 Collection → 全空间搜索
  1 个 SQLite KV 数据库 → Memory 对象存储
"""

import sys
import os
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from models.xcmemory_interest.basic_crud import VecDBCRUD, EmbeddingMode


def main():
    test_dir = os.path.join(os.path.dirname(__file__), 'vec_db_test_data')

    if os.path.exists(test_dir):
        shutil.rmtree(test_dir, ignore_errors=True)

    print('=' * 70)
    print('VecDBCRUD 完整演示')
    print('=' * 70)

    # ================================================================
    # 1. 初始化
    # ================================================================
    print('\n[1] 初始化 VecDBCRUD')
    db = VecDBCRUD(persist_directory=test_dir)
    print(f'    持久化目录: {test_dir}')
    print(f'    初始记忆数: {db.count()}')

    # ================================================================
    # 2. 写入
    # ================================================================
    print('\n[2] 写入记忆')
    memories = [
        ('<平时><我><学><编程><喜欢><有收获>',     '我平时喜欢学编程'),
        ('<周末><我><打><篮球><锻炼身体><很爽>',   '周末打篮球'),
        ('<晚上><我><看><书><学习知识><进步>',     '晚上看书学习'),
        ('<假期><我><去><旅行><放松心情><开心>',   '假期去旅行'),
        ('<平时><我><写><代码><工作需要><完成任务>', '平时写代码工作'),
        ('<周末><我><做饭><美食><享受生活><满足>',  '周末做饭享受生活'),
        ('<晚上><我><散步><公园><锻炼身体><健康>',  '晚上散步公园'),
        ('<假期><朋友><约><我><一起玩><很开心>',    '朋友约我玩'),
    ]

    ids = []
    for query, content in memories:
        mid = db.write(query, content, lifecycle=30, embedding_mode=EmbeddingMode.INTEREST)
        ids.append(mid)
        print(f'    写入: {query} → {mid}')

    print(f'\n    共写入 {db.count()} 条记忆')
    print(f'    各槽位 Collection 向量数: {db.slot_counts}')
    print(f'    全量 Collection 向量数: {db.full_count}')

    # ================================================================
    # 3. 读取
    # ================================================================
    print('\n[3] 读取记忆')
    mem = db.read(ids[0])
    print(f'    read({ids[0]})')
    print(f'    query_sentence: {mem.query_sentence}')
    print(f'    content: {mem.content}')
    print(f'    lifecycle: {mem.lifecycle}')

    # ================================================================
    # 4. 更新
    # ================================================================
    print('\n[4] 更新记忆')
    db.update(ids[0], content='更新后：我平时超喜欢学编程', lifecycle=60)
    mem2 = db.read(ids[0])
    print(f'    update({ids[0]}, content=..., lifecycle=60)')
    print(f'    content: {mem2.content}')
    print(f'    lifecycle: {mem2.lifecycle}')

    # ================================================================
    # 5. 子空间搜索 - 单槽位
    # ================================================================
    print('\n[5] 子空间搜索 - 单槽位 (subject=我)')
    results = db.search_subspace(query_slots={'subject': '我'}, top_k=5, use_slot_rerank=True)
    print(f'    结果数: {len(results)}')
    for i, r in enumerate(results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs}  匹配={r.match_count}  avg_dist={r.avg_distance:.4f}  sort={r.sort_by}')

    # ================================================================
    # 6. 子空间搜索 - 多槽位（交集）
    # ================================================================
    print('\n[6] 子空间搜索 - 多槽位 (subject=我 ∧ purpose=锻炼身体)')
    results = db.search_subspace(
        query_slots={'subject': '我', 'purpose': '锻炼身体'},
        top_k=5,
        use_slot_rerank=True,
    )
    print(f'    结果数: {len(results)}')
    for i, r in enumerate(results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs}  匹配={r.match_count}  avg_dist={r.avg_distance:.4f}')

    # ================================================================
    # 7. 子空间搜索 - 不排序
    # ================================================================
    print('\n[7] 子空间搜索 - 不排序 (subject=我, use_slot_rerank=False)')
    results = db.search_subspace(query_slots={'subject': '我'}, top_k=5, use_slot_rerank=False)
    for i, r in enumerate(results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs}  dist={r.distance:.4f}  sort={r.sort_by}')

    # ================================================================
    # 8. 全空间搜索
    # ================================================================
    print('\n[8] 全空间搜索 (subject=我, purpose=锻炼身体)')
    results = db.search_fullspace(
        query_slots={'subject': '我', 'purpose': '锻炼身体'},
        top_k=5,
        embedding_mode=EmbeddingMode.INTEREST,
        use_slot_rerank=True,
    )
    for i, r in enumerate(results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs}  匹配={r.match_count}  dist={r.distance:.4f}')

    # ================================================================
    # 9. 全空间搜索 - 不排序
    # ================================================================
    print('\n[9] 全空间搜索 - 不排序')
    results = db.search_fullspace(
        query_slots={'subject': '我'},
        top_k=5,
        embedding_mode=EmbeddingMode.INTEREST,
        use_slot_rerank=False,
    )
    for i, r in enumerate(results):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'    [{i}] {qs}  dist={r.distance:.4f}')

    # ================================================================
    # 10. 删除
    # ================================================================
    print('\n[10] 删除记忆')
    print(f'    删除前: {db.count()} 条')
    db.delete(ids[0])
    print(f'    delete({ids[0]})')
    print(f'    删除后: {db.count()} 条')
    mem3 = db.read(ids[0])
    print(f'    read({ids[0]}) → {mem3}')

    # ================================================================
    # 11. 子空间 vs 全空间 对比
    # ================================================================
    print('\n[11] 子空间 vs 全空间 对比 (subject=我, action=散步)')
    sub = db.search_subspace(
        query_slots={'subject': '我', 'action': '散步'},
        top_k=3, use_slot_rerank=True,
    )
    full = db.search_fullspace(
        query_slots={'subject': '我', 'action': '散步'},
        top_k=3, embedding_mode=EmbeddingMode.INTEREST, use_slot_rerank=True,
    )
    print('    子空间:')
    for i, r in enumerate(sub):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'      [{i}] {qs}  匹配={r.match_count}  dist={r.avg_distance:.4f}')
    print('    全空间:')
    for i, r in enumerate(full):
        qs = r.memory.query_sentence if r.memory else '?'
        print(f'      [{i}] {qs}  匹配={r.match_count}  dist={r.distance:.4f}')

    # ================================================================
    # 清理
    # ================================================================
    db.close()
    shutil.rmtree(test_dir, ignore_errors=True)

    print('\n' + '=' * 70)
    print('演示完成！')
    print('=' * 70)


if __name__ == '__main__':
    main()
