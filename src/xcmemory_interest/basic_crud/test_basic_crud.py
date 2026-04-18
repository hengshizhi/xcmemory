"""
星尘记忆 - BasicCRUD 模块测试

测试两种嵌入模式：INTEREST 和 RAW
"""

import sys
import os
import tempfile

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcmemory_interest.basic_crud import BasicCRUD, EmbeddingMode


def test_basic_crud():
    """测试 BasicCRUD 基本功能"""

    # 使用临时目录
    with tempfile.TemporaryDirectory() as tmp_dir:
        kv_dir = os.path.join(tmp_dir, "kv")
        vec_dir = os.path.join(tmp_dir, "vector_db")

        crud = BasicCRUD(
            persist_directory=kv_dir,
            vector_db_path=vec_dir,
        )

        print("=" * 60)
        print("星尘记忆系统 - BasicCRUD 模块测试")
        print("=" * 60)

        # ================================================================
        # 1. 测试写入（INTEREST 模式）
        # ================================================================
        print("\n[1] 测试写入（INTEREST 模式）")

        memories = [
            ("<平时><我><学><编程><喜欢><有收获>", "我平时喜欢学编程，学了很有收获", 30),
            ("<周末><我><打><篮球><锻炼身体><很爽>", "周末我喜欢打篮球，锻炼身体很爽", 20),
            ("<晚上><我><看><书><学习知识><进步>", "晚上我喜欢看书学习知识，进步很快", 25),
            ("<假期><我><去><旅行><放松心情><开心>", "假期我喜欢去旅行，放松心情很开心", 15),
            ("<平时><我><写><代码><工作需要><完成任务>", "平时我写代码，因为工作需要，任务完成得很好", 30),
            ("<周末><我><做饭><美食><享受生活><满足>", "周末我喜欢做饭，享受美食，生活很满足", 20),
            ("<晚上><我><散步><公园><锻炼身体><健康>", "晚上我喜欢在公园散步，锻炼身体保持健康", 25),
            ("<假期><朋友><约><我><一起玩><很开心>", "假期朋友约我一起玩，我们很开心", 15),
        ]

        memory_ids = []
        for query, content, lifecycle in memories:
            memory_id = crud.write(
                query_sentence=query,
                content=content,
                lifecycle=lifecycle,
                embedding_mode=EmbeddingMode.INTEREST,
            )
            memory_ids.append(memory_id)
            print(f"  Added: {memory_id[:20]}... -> {query[:30]}...")

        print(f"\n当前记忆总数: {crud.count()}")

        # ================================================================
        # 2. 测试读取
        # ================================================================
        print("\n[2] 测试读取")

        memory = crud.read(memory_ids[0])
        print(f"  ID: {memory.id}")
        print(f"  Query: {memory.query_sentence}")
        print(f"  Content: {memory.content}")
        print(f"  Lifecycle: {memory.lifecycle}")
        print(f"  Interest Embedding shape: {memory.query_embedding.shape}")
        print(f"  Raw Embedding shape: {memory.raw_embedding.shape}")

        # ================================================================
        # 3. 测试更新
        # ================================================================
        print("\n[3] 测试更新")

        success = crud.update(memory_ids[0], content="更新后的内容", lifecycle=10)
        print(f"  更新成功: {success}")

        memory = crud.read(memory_ids[0])
        print(f"  新内容: {memory.content}")
        print(f"  新生命周期: {memory.lifecycle}")

        # ================================================================
        # 4. 测试搜索（全空间 - INTEREST 模式）
        # ================================================================
        print("\n[4] 测试搜索（全空间 - INTEREST 模式）")

        print("\n  查询1: subject=我, action=学")
        results = crud.search_fullspace(
            query_slots={"subject": "我", "action": "学"},
            top_k=3,
            embedding_mode=EmbeddingMode.INTEREST,
        )
        for r in results:
            print(f"    - {r.memory.query_sentence[:40]} (dist: {r.distance:.4f})")

        print("\n  查询2: time=周末")
        results = crud.search_fullspace(
            query_slots={"time": "周末"},
            top_k=3,
            embedding_mode=EmbeddingMode.INTEREST,
        )
        for r in results:
            print(f"    - {r.memory.query_sentence[:40]} (dist: {r.distance:.4f})")

        # ================================================================
        # 5. 测试搜索（全空间 - RAW 模式）
        # ================================================================
        print("\n[5] 测试搜索（全空间 - RAW 模式）")

        print("\n  查询1: subject=我, action=学")
        results = crud.search_fullspace(
            query_slots={"subject": "我", "action": "学"},
            top_k=3,
            embedding_mode=EmbeddingMode.RAW,
        )
        for r in results:
            print(f"    - {r.memory.query_sentence[:40]} (dist: {r.distance:.4f})")

        # ================================================================
        # 6. 测试子空间搜索
        # ================================================================
        print("\n[6] 测试子空间搜索")

        print("\n  查询: subject=我, purpose=健康")
        results = crud.search_subspace(
            query_slots={"subject": "我", "purpose": "健康"},
            top_k=3,
            embedding_mode=EmbeddingMode.INTEREST,
            rerank=True,
        )
        for r in results:
            print(f"    - {r.memory.query_sentence[:40]} (dist: {r.distance:.4f})")

        # ================================================================
        # 7. 测试删除
        # ================================================================
        print("\n[7] 测试删除")

        print(f"  删除前总数: {crud.count()}")
        success = crud.delete(memory_ids[0])
        print(f"  删除成功: {success}")
        print(f"  删除后总数: {crud.count()}")

        # ================================================================
        # 8. 验证 memory_id 不存在
        # ================================================================
        print("\n[8] 验证删除")

        memory = crud.read(memory_ids[0])
        print(f"  读取已删除的 memory: {memory}")

        # ================================================================
        # 关闭连接
        # ================================================================
        crud.close()

        print("\n" + "=" * 60)
        print("测试完成！")
        print("=" * 60)


if __name__ == "__main__":
    test_basic_crud()
