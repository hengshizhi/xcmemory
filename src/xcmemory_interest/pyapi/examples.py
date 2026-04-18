"""
Starlight Memory System - PyAPI Usage Examples
================================================

Query sentence format:
    "<time><subject><action><object><purpose><result>"

Usage:
    python examples.py
"""

import sys
sys.path.insert(0, 'o:/project/starlate/models')

import tempfile
import shutil
from datetime import datetime, timedelta
from xcmemory_interest.pyapi import PyAPI


# ============================================================================
# Example 1: Basic CRUD Operations
# ============================================================================

def example_basic_crud():
    """Basic memory write and read"""
    print("\n" + "=" * 60)
    print("Example 1: Basic CRUD Operations")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        api = PyAPI(persist_directory=test_dir, vocab_size=10000)

        # Create memory system (interest mode disabled for simplicity)
        sys1 = api.create_system(
            name="demo_system",
            enable_interest_mode=False,
            similarity_threshold=0.85,
        )

        # Write memory - full query sentence format
        mem_id1 = sys1.write(
            query_sentence="<平时><我><学习><Python><提升><成长>",
            content="Today I learned Python crawler",
            lifecycle=86400,  # 1 day
        )
        print(f"Write memory 1: {mem_id1}")

        # Write memory - content can be empty
        mem_id2 = sys1.write(
            query_sentence="<平时><我><跑步><锻炼><健康><坚持>",
            content="",
            lifecycle=3600,  # 1 hour
        )
        print(f"Write memory 2: {mem_id2}")

        # Read memory
        mem = sys1.get_memory(mem_id1)
        print(f"\nRead memory:")
        print(f"  ID: {mem.id}")
        print(f"  Query: {mem.query_sentence}")
        print(f"  Content: {mem.content}")
        print(f"  Lifecycle: {mem.lifecycle} seconds")
        print(f"  Created: {mem.created_at}")

        # Update memory
        ok = sys1.update(mem_id1, content="Updated: Learned Python async programming")
        print(f"\nUpdate: {'OK' if ok else 'Failed'}")

        # Delete memory
        ok = sys1.delete(mem_id2)
        print(f"\nDelete: {'OK' if ok else 'Failed'}")
        print(f"  Remaining: {sys1.count()} memories")

        api.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 1 done")


# ============================================================================
# Example 2: Various Search Methods
# ============================================================================

def example_search_methods():
    """Show subspace search, fullspace search, time index search"""
    print("\n" + "=" * 60)
    print("Example 2: Various Search Methods")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        api = PyAPI(persist_directory=test_dir, vocab_size=10000)
        sys1 = api.create_system("search_demo", enable_interest_mode=False)

        # Write test memories
        test_memories = [
            ("<平时><我><学习><Python><提升><成长>", "Learn Python", 86400),
            ("<平时><我><学习><Java><提升><成长>", "Learn Java", 86400),
            ("<平时><我><学习><Go><提升><成长>", "Learn Go", 86400),
            ("<平时><我><跑步><锻炼><健康><坚持>", "Running", 3600),
            ("<平时><我><写作><博客><分享><交流>", "Write blog", 86400),
            ("<最近><我><阅读><技术书籍><知识><积累>", "Read book", 172800),
        ]

        for query, content, lc in test_memories:
            sys1.write(query, content, lc)

        print(f"Written {len(test_memories)} test memories\n")

        # Subspace search - search each slot independently, take intersection
        print("[Subspace Search] subject='我', action='学习'")
        results = sys1.search_subspace(
            query_slots={"subject": "我", "action": "学习"},
            top_k=10,
        )
        for r in results:
            print(f"  - ID={r.memory_id}, match={r.match_count}, dist={r.distance:.3f}")
        print()

        # Fullspace search - search in 384-dim space
        print("[Fullspace Search] subject='我'")
        results = sys1.search_fullspace(
            query_slots={"subject": "我"},
            top_k=10,
        )
        for r in results:
            print(f"  - ID={r.memory_id}, dist={r.distance:.3f}")
        print()

        # Time word search
        print("[Time Word Search] 平时/最近")
        ids = sys1.search_by_time_words(["平时", "最近"], fuzzy=True)
        print(f"  Found {len(ids)} memories")
        print()

        # Time range search
        print("[Time Range Search] Last 7 days")
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        ids = sys1.search_by_time_range(week_ago, now)
        print(f"  Found {len(ids)} memories")
        print()

        # Slot value search
        print("[Slot Search] word='我', slot=subject")
        ids = sys1.search_by_slot_value(word="我", slot="subject")
        print(f"  Found {len(ids)} memories")

        api.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 2 done")


# ============================================================================
# Example 3: Lifecycle Management
# ============================================================================

def example_lifecycle_management():
    """Show lifecycle queries and expired memory cleanup"""
    print("\n" + "=" * 60)
    print("Example 3: Lifecycle Management")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        api = PyAPI(persist_directory=test_dir, vocab_size=10000)
        sys1 = api.create_system("lifecycle_demo", enable_interest_mode=False)

        # Write memories with different lifecycles
        sys1.write("<平时><我><学习><编程><提升><成长>", "Short-term", lifecycle=3600)
        sys1.write("<平时><我><工作><任务><完成><成就>", "Mid-term", lifecycle=86400)
        sys1.write("<平时><我><阅读><书籍><知识><智慧>", "Long-term", lifecycle=7*86400)
        sys1.write("<平时><我><人生><思考><意义><价值>", "Permanent", lifecycle=999999999)

        # Get statistics
        print("[System Statistics]")
        stats = sys1.get_stats()
        print(f"  Total: {stats['total']}")
        print(f"  Alive: {stats['alive']}")
        print(f"  Expired: {stats['expired']}")
        print(f"  Infinite: {stats['infinite']}")
        print(f"  Distribution: {stats['lifecycle_distribution']}")
        print()

        # Query by lifecycle range
        print("[Lifecycle Range Query] 1 day ~ 7 days")
        results = sys1.search_by_lifecycle(min_lifecycle=86400, max_lifecycle=7*86400)
        for r in results:
            print(f"  - ID={r.memory_id}, lc={r.lifecycle}, expired={r.is_expired}")
        print()

        # Query infinite lifecycle
        print("[Infinite Lifecycles]")
        ids = sys1.search_infinite_lifecycle()
        print(f"  Found {len(ids)}")
        print()

        # Cleanup expired memories (dry run)
        print("[Cleanup Expired Memories]")
        print("  Dry run - check what would be deleted:")
        to_delete = sys1.delete_expired(dry_run=True)
        print(f"    {len(to_delete)} memories: {to_delete}")

        # Actually delete
        deleted = sys1.delete_expired(dry_run=False)
        print(f"  Deleted: {len(deleted)}")
        print(f"  Remaining: {sys1.count()}")

        api.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 3 done")


# ============================================================================
# Example 4: Multi-System Management
# ============================================================================

def example_multi_system():
    """Show how to manage multiple independent memory systems"""
    print("\n" + "=" * 60)
    print("Example 4: Multi-System Management")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        api = PyAPI(persist_directory=test_dir, vocab_size=10000)

        # Create multiple systems
        # System 1: Work memory (interest mode enabled)
        work_sys = api.create_system(
            name="work_memory",
            enable_interest_mode=True,
            similarity_threshold=0.85,
        )

        # System 2: Life memory (interest mode disabled)
        life_sys = api.create_system(
            name="life_memory",
            enable_interest_mode=False,
        )

        print(f"Created systems: {api.list_systems()}")

        # Write to different systems
        work_sys.write("<工作日><同事><代码评审><代码><建议><改进>", "Completed important project", lifecycle=86400)
        work_sys.write("<周会><团队><讨论><架构><决策><方向>", "Discussed quarterly plan", lifecycle=3600)

        life_sys.write("<周末><家人><做饭><美食><享受><生活>", "Made braised pork today", lifecycle=86400)
        life_sys.write("<晚上><朋友><看电影><科幻><放松><娱乐>", "Watched movie", lifecycle=172800)

        print(f"Work system: {work_sys.count()} memories")
        print(f"Life system: {life_sys.count()} memories")
        print()

        # Switch active system
        print("[Switch Active System]")
        api.set_active_system("work_memory")
        print(f"Current active: {api.active_system_name}")

        api.set_active_system("life_memory")
        print(f"After switch: {api.active_system_name}")
        print()

        # Use get_or_create_system
        print("[get_or_create_system]")
        existing = api.get_or_create_system("work_memory")
        print(f"Got existing: {existing.name}")

        new_sys = api.get_or_create_system("study_memory", enable_interest_mode=False)
        print(f"Created new: {new_sys.name}")
        print(f"All systems: {api.list_systems()}")
        print()

        # Delete system
        print("[Delete System]")
        ok = api.delete_system("study_memory")
        print(f"Deleted: {'OK' if ok else 'Failed'}")
        print(f"Remaining: {api.list_systems()}")

        api.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 4 done")


# ============================================================================
# Example 5: Interest Mode Details
# ============================================================================

def example_interest_mode():
    """Show how interest mode works"""
    print("\n" + "=" * 60)
    print("Example 5: Interest Mode Details")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        # Disable interest mode
        sys_no_interest = PyAPI(persist_directory=test_dir, vocab_size=10000).create_system(
            "no_interest",
            enable_interest_mode=False,
        )

        # Enable interest mode
        sys_interest = PyAPI(persist_directory=test_dir, vocab_size=10000).create_system(
            "with_interest",
            enable_interest_mode=True,
        )

        # Write same query
        query = "<平时><我><学习><Python><提升><成长>"
        lifecycle_input = 86400

        # Disable mode: lifecycle = input value
        mid1 = sys_no_interest.write(query, "No interest mode", lifecycle=lifecycle_input)
        mem1 = sys_no_interest.get_memory(mid1)
        print(f"Interest OFF: input lc={lifecycle_input}, actual lc={mem1.lifecycle}")

        # Enable mode: lifecycle calculated by LifecycleManager
        mid2 = sys_interest.write(query, "With interest mode", lifecycle=lifecycle_input)
        mem2 = sys_interest.get_memory(mid2)
        print(f"Interest ON: input lc={lifecycle_input}, actual lc={mem2.lifecycle}")
        print()

        # Similarity filter
        print("[Similarity Filter]")
        print(f"Threshold: {sys_interest.similarity_threshold}")

        # Write first memory (completely different topic)
        q1 = "<最近><我><阅读><小说><娱乐><放松>"
        sys_interest.write(q1, "Reading novel")

        # Try to write very similar memory (should be rejected)
        q2 = "<最近><我><阅读><小说><娱乐><休闲>"  # Only last slot differs
        try:
            sys_interest.write(q2, "Reading another novel")
            print("  Write succeeded (not similar enough)")
        except ValueError as e:
            print(f"  Write rejected: similarity too high")
        print()

        # Access trigger
        print("[Access Trigger]")
        updates = sys_interest.on_memory_accessed(mid2)
        if updates:
            print(f"  Triggered {len(updates)} lifecycle updates")
            for old_id, old_lc, new_lc in updates:
                print(f"    - {old_id}: {old_lc} -> {new_lc}")

        sys_no_interest.close()
        sys_interest.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 5 done")


# ============================================================================
# Example 6: Real-World Scenario
# ============================================================================

def example_real_world_scenario():
    """Personal knowledge management system simulation"""
    print("\n" + "=" * 60)
    print("Example 6: Real-World Scenario - Knowledge Base")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix='xcmemory_demo_')

    try:
        api = PyAPI(persist_directory=test_dir, vocab_size=10000)
        kms = api.create_system(
            name="knowledge_base",
            enable_interest_mode=True,
            similarity_threshold=0.0,  # Disable similarity filter for demo
        )

        print("\n[Record Learning Notes]")

        learnings = [
            ("<平时><我><学习><Python><掌握><技能>", "Python decorators are powerful"),
            ("<周末><我><学习><Go><掌握><并发>", "Go goroutines are efficient"),
            ("<最近><我><阅读><系统设计><提升><架构能力>", "Good design considers scalability"),
            ("<每天><我><练习><算法><巩固><基础>", "Practice algorithms daily"),
        ]

        for query, content in learnings:
            mid = kms.write(query, content, lifecycle=7*86400)
            print(f"  + {mid[:16]}... -> {query}")

        print("\n[Record Work Notes]")

        work_notes = [
            ("<平时><我><代码审查><Bug><发现><修复>", "Code review found security issue"),
            ("<最近><我><开会><讨论><微服务><方案>", "Microservices discussion"),
        ]

        for query, content in work_notes:
            mid = kms.write(query, content, lifecycle=30*86400)
            print(f"  + {mid[:16]}... -> {query[1:30]}...")

        print("\n[Query Learning Related]")
        results = kms.search_subspace(
            query_slots={"action": "学习"},
            top_k=5,
        )
        print(f"  Found {len(results)} learning memories:")
        for r in results:
            mem = kms.get_memory(r.memory_id)
            print(f"    - {mem.content[:40]}...")

        print("\n[Query Python Related]")
        results = kms.search_subspace(
            query_slots={"object": "Python"},
            top_k=5,
        )
        print(f"  Found {len(results)}:")
        for r in results:
            mem = kms.get_memory(r.memory_id)
            print(f"    - {mem.content[:40]}...")

        print("\n[Periodic Cleanup]")
        stats = kms.get_stats()
        print(f"  Before: Total {stats['total']}, Alive {stats['alive']}")

        deleted = kms.delete_expired(dry_run=False)
        print(f"  Cleanup: Deleted {len(deleted)} expired memories")

        stats = kms.get_stats()
        print(f"  After: Total {stats['total']}, Alive {stats['alive']}")

        api.close()

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n[PASS] Example 6 done")


# ============================================================================
# Run All Examples
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Starlight Memory System - PyAPI Examples")
    print("=" * 60)

    example_basic_crud()
    example_search_methods()
    example_lifecycle_management()
    example_multi_system()
    example_interest_mode()
    example_real_world_scenario()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
