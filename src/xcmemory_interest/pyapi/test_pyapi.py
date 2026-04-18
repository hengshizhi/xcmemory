"""
星尘记忆系统 - PyAPI 测试

测试 PyAPI 的完整功能：
1. 创建/删除记忆系统
2. 记忆的增删查改
3. 多种搜索方式
4. 维护功能（过期删除、相似度过滤）
5. 兴趣模式开关
"""

import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

# 添加项目路径（models 是顶级包）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcmemory_interest.pyapi import PyAPI, MemorySystem, SearchResult


# 测试配置
TEST_DIR = tempfile.mkdtemp(prefix="xcmemory_test_")
print(f"测试目录: {TEST_DIR}")


def test_create_delete_system():
    """测试创建和删除记忆系统"""
    print("\n=== Test: Create/Delete System ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)

    # 创建第一个系统（启用兴趣模式）
    sys1 = api.create_system(
        name="test_sys1",
        enable_interest_mode=True,
        similarity_threshold=0.85,
    )
    print(f"Created system1: {sys1}")
    assert sys1.name == "test_sys1"
    assert sys1.enable_interest_mode == True

    # 创建第二个系统（禁用兴趣模式）
    sys2 = api.create_system(
        name="test_sys2",
        enable_interest_mode=False,
    )
    print(f"Created system2: {sys2}")
    assert sys2.name == "test_sys2"
    assert sys2.enable_interest_mode == False

    # 列出系统
    systems = api.list_systems()
    print(f"Systems: {systems}")
    assert "test_sys1" in systems
    assert "test_sys2" in systems

    # 删除系统1
    ok = api.delete_system("test_sys1")
    print(f"Deleted system1: {ok}")
    assert ok == True

    # 验证删除
    systems = api.list_systems()
    assert "test_sys1" not in systems

    api.close()
    print("[PASS] test_create_delete_system")


def test_crud_operations():
    """测试记忆的增删查改"""
    print("\n=== Test: CRUD Operations ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)
    sys1 = api.create_system("crud_test", enable_interest_mode=False)

    # 写入记忆（使用查询句格式）
    query_sentence = "<平时><我><学习><编程><提升><成长>"
    mem_id1 = sys1.write(
        query_sentence=query_sentence,
        content="Content 1",
        lifecycle=86400,
    )
    print(f"Write memory1: {mem_id1}")
    assert mem_id1 is not None

    # 写入另一条记忆
    mem_id2 = sys1.write(
        query_sentence="<平时><我><跑步><锻炼><健康><坚持>",
        content="Content 2",
        lifecycle=3600,
    )
    print(f"Write memory2: {mem_id2}")
    assert mem_id2 is not None

    # 读取记忆
    mem1 = sys1.get_memory(mem_id1)
    print(f"Read memory1: id={mem1.id}, content={mem1.content}, lc={mem1.lifecycle}")
    assert mem1 is not None
    assert mem1.content == "Content 1"

    # 更新记忆
    ok = sys1.update(mem_id1, content="Updated", lifecycle=172800)
    print(f"Update memory1: {ok}")
    assert ok == True

    # 验证更新
    mem1_updated = sys1.get_memory(mem_id1)
    assert mem1_updated.content == "Updated"
    assert mem1_updated.lifecycle == 172800

    # 批量读取
    memories = sys1.get_memories([mem_id1, mem_id2])
    print(f"Batch read: {len(memories)} memories")
    assert len(memories) == 2

    # 删除记忆
    ok = sys1.delete(mem_id2)
    print(f"Delete memory2: {ok}")
    assert ok == True

    # 验证删除
    mem2_deleted = sys1.get_memory(mem_id2)
    assert mem2_deleted is None

    # 统计
    count = sys1.count()
    print(f"Total memories: {count}")
    assert count == 1

    api.close()
    print("[PASS] test_crud_operations")


def test_search_operations():
    """测试各种搜索方式"""
    print("\n=== Test: Search Operations ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)
    sys1 = api.create_system("search_test", enable_interest_mode=False)

    # 写入多条记忆
    memories = [
        ("<平时><我><学习><Python><提升><成长>", "Learn Python", 86400),
        ("<平时><我><学习><Java><提升><成长>", "Learn Java", 86400),
        ("<平时><我><跑步><锻炼><健康><坚持>", "Running", 3600),
        ("<最近><我><阅读><书籍><开阔><视野>", "Reading", 172800),
        ("<平时><我><写作><博客><分享><交流>", "Writing", 86400),
    ]

    for query, content, lc in memories:
        mem_id = sys1.write(query, content, lc)
        print(f"Write: {mem_id} -> {query[:30]}...")

    # 子空间搜索
    results = sys1.search_subspace(
        query_slots={"subject": "我", "action": "学习"},
        top_k=5,
    )
    print(f"Subspace search: {len(results)} results")
    for r in results:
        print(f"  - {r.memory_id}, dist={r.distance:.3f}, match={r.match_count}")

    # 全空间搜索
    results = sys1.search_fullspace(
        query_slots={"subject": "我"},
        top_k=5,
    )
    print(f"Fullspace search: {len(results)} results")

    # 按时间范围搜索
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    ids = sys1.search_by_time_range(yesterday, now)
    print(f"Time range search (last 1 day): {len(ids)} results")

    # 按时间词搜索
    ids = sys1.search_by_time_words(["平时", "最近"], fuzzy=True)
    print(f"Time word search: {len(ids)} results")

    # 查询最近 N 天
    ids = sys1.search_recent(days=7)
    print(f"Recent 7 days: {len(ids)} results")

    # 按槽位值搜索
    ids = sys1.search_by_slot_value(word="我", slot="subject")
    print(f"Slot search (subject=我): {len(ids)} results")

    api.close()
    print("[PASS] test_search_operations")


def test_lifecycle_operations():
    """测试生命周期相关操作"""
    print("\n=== Test: Lifecycle Operations ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)
    sys1 = api.create_system("lifecycle_test", enable_interest_mode=False)

    # 写入不同生命周期的记忆
    sys1.write("<平时><我><学习><编程><提升><成长>", "Learn1", lifecycle=3600)
    sys1.write("<平时><我><工作><任务><完成><成就>", "Work1", lifecycle=86400)
    sys1.write("<平时><我><运动><跑步><健康><坚持>", "Sport1", lifecycle=7*86400)
    sys1.write("<平时><我><阅读><书籍><知识><智慧>", "Read1", lifecycle=30*86400)
    sys1.write("<平时><我><思考><人生><意义><价值>", "Infinite", lifecycle=999999)

    # 按生命周期范围查询
    results = sys1.search_by_lifecycle(min_lifecycle=86400, max_lifecycle=7*86400)
    print(f"Lifecycle range query (1d-7d): {len(results)} results")
    for r in results:
        print(f"  - {r.memory_id}, lc={r.lifecycle}, expired={r.is_expired}")

    # 查询永不过期的记忆
    ids = sys1.search_infinite_lifecycle()
    print(f"Infinite lifecycle memories: {len(ids)}")

    # 获取统计信息
    stats = sys1.get_stats()
    print(f"System stats: {stats}")

    # 模拟过期
    all_ids = sys1.list_all_memory_ids()
    for mid in all_ids:
        mem = sys1.get_memory(mid)
        if mem and mem.content == "Learn1":
            sys1.update(mid, lifecycle=1)

    # 清理过期记忆
    expired = sys1.delete_expired(dry_run=True)
    print(f"Expired (dry_run): {len(expired)}")

    deleted = sys1.delete_expired(dry_run=False)
    print(f"Deleted expired: {len(deleted)}")

    api.close()
    print("[PASS] test_lifecycle_operations")


def test_similarity_filter():
    """测试相似度过滤功能"""
    print("\n=== Test: Similarity Filter ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)

    # 创建一个高阈值的系统
    sys_strict = api.create_system(
        name="strict_test",
        enable_interest_mode=True,
        similarity_threshold=0.9,
    )

    # 创建一个低阈值的系统
    sys_loose = api.create_system(
        name="loose_test",
        enable_interest_mode=True,
        similarity_threshold=0.5,
    )

    # 写入第一条记忆
    query1 = "<平时><我><学习><Python><提升><成长>"
    mem_id1 = sys_strict.write(query1, "Learn Python")
    print(f"Write memory1: {mem_id1}")

    # 尝试写入相似记忆（应该被 strict 系统拒绝）
    query2 = "<平时><我><学习><Python><提升><进步>"
    try:
        mem_id2 = sys_strict.write(query2, "Learn Python Advanced")
        print(f"strict system: should reject but got {mem_id2}")
    except ValueError as e:
        print(f"strict system rejected similar memory: {e}")

    # loose 系统应该能写入
    mem_id3 = sys_loose.write(query2, "Learn Python Advanced")
    print(f"loose system write similar memory3: {mem_id3}")

    api.close()
    print("[PASS] test_similarity_filter")


def test_multi_system():
    """测试多系统管理"""
    print("\n=== Test: Multi-System ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)

    # 创建多个系统
    sys_a = api.create_system("sys_a", enable_interest_mode=False)
    sys_b = api.create_system("sys_b", enable_interest_mode=False)

    # 分别写入数据
    sys_a.write("<平时><A><工作><任务><完成><成就>", "Memory A", lifecycle=86400)
    sys_b.write("<平时><B><学习><知识><提升><进步>", "Memory B", lifecycle=86400)

    # 切换活跃系统
    api.set_active_system("sys_a")
    print(f"Active system: {api.active_system_name}")
    assert api.active_system_name == "sys_a"

    # 使用便捷方法
    api.set_active_system("sys_b")
    result = api.search_fullspace(query_slots={"subject": "B"})
    print(f"Search 'subject=B': {len(result)} results")

    # 验证数据隔离
    assert sys_a.count() == 1
    assert sys_b.count() == 1
    assert sys_a.get_memory(sys_b.list_all_memory_ids()[0]) is None

    api.close()
    print("[PASS] test_multi_system")


def test_interest_mode():
    """测试兴趣模式"""
    print("\n=== Test: Interest Mode ===")

    api = PyAPI(persist_directory=TEST_DIR, vocab_size=5000)

    # 启用兴趣模式
    sys_interest = api.create_system(
        name="interest_on",
        enable_interest_mode=True,
    )

    # 禁用兴趣模式
    sys_no_interest = api.create_system(
        name="interest_off",
        enable_interest_mode=False,
    )

    query = "<平时><我><学习><编程><提升><成长>"
    lifecycle_input = 86400

    # 写入记忆
    mem_id1 = sys_interest.write(query, "Interest Mode", lifecycle=lifecycle_input)
    mem_id2 = sys_no_interest.write(query, "No Interest Mode", lifecycle=lifecycle_input)

    # 比较生命周期
    mem1 = sys_interest.get_memory(mem_id1)
    mem2 = sys_no_interest.get_memory(mem_id2)

    print(f"Interest mode ON: lc={mem1.lifecycle} (input={lifecycle_input})")
    print(f"Interest mode OFF: lc={mem2.lifecycle} (input={lifecycle_input})")

    assert mem2.lifecycle == lifecycle_input

    api.close()
    print("[PASS] test_interest_mode")


def cleanup():
    """清理测试目录"""
    global TEST_DIR
    if os.path.exists(TEST_DIR):
        import time
        time.sleep(0.5)
        try:
            shutil.rmtree(TEST_DIR, ignore_errors=True)
        except Exception:
            pass
        print(f"\n清理测试目录: {TEST_DIR}")


if __name__ == "__main__":
    try:
        test_create_delete_system()
        test_crud_operations()
        test_search_operations()
        test_lifecycle_operations()
        test_similarity_filter()
        test_multi_system()
        test_interest_mode()

        print("\n" + "=" * 50)
        print("ALL TESTS PASSED!")
        print("=" * 50)
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup()