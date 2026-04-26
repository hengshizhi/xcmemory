"""
auxiliary_query 全功能测试
测试 KVDatabase, SQLDatabase, Interpreter, Scheduler, TimeIndex, SlotIndex
"""

import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auxiliary_query import (
    KVDatabase,
    SQLDatabase,
    Interpreter,
    Scheduler,
    TimeIndex,
    SlotIndex,
)
from auxiliary_query.indexes.slot_index import SLOT_NAMES as SLOT_NAMES_CONST


def test_kv_database(tmp_dir):
    """测试 KVDatabase (LMDB)"""
    print("\n" + "="*60)
    print("测试 KVDatabase (LMDB)")
    print("="*60)

    db = KVDatabase(persist_directory=tmp_dir, db_name="test_kv")

    # 基础操作
    print("\n[1] 基础操作测试")
    db.set("key1", {"name": "张三", "age": 30})
    db.set("key2", "hello world")
    db.set("key3", [1, 2, 3, 4, 5])

    value1 = db.get("key1")
    assert value1 == {"name": "张三", "age": 30}, f"获取值失败: {value1}"

    value2 = db.get("key2")
    assert value2 == "hello world", f"获取值失败: {value2}"

    value3 = db.get("key3")
    assert value3 == [1, 2, 3, 4, 5], f"获取值失败: {value3}"
    print("  [OK] set/get 基本读写")

    # 存在性检查
    assert db.exists("key1") == True
    assert db.exists("nonexistent") == False
    print("  [OK] exists 存在性检查")

    # 默认值
    default_val = db.get("nonexistent", default="default_value")
    assert default_val == "default_value", f"默认值测试失败: {default_val}"
    print("  [OK] get 默认值")

    # 删除
    db.delete("key2")
    assert db.get("key2") is None
    print("  [OK] delete 删除")

    # 批量操作
    print("\n[2] 批量操作测试")
    db.mset({"batch1": "value1", "batch2": "value2", "batch3": "value3"})
    batch_result = db.mget(["batch1", "batch2", "batch3", "nonexistent"])
    assert len(batch_result) == 3
    assert batch_result["batch1"] == "value1"
    print("  [OK] mset/mget 批量读写")

    # 模糊查询 keys
    all_keys = db.keys("batch*")
    assert len(all_keys) == 3
    print(f"  [OK] keys 模糊查询: {all_keys}")

    # mdelete
    deleted = db.mdelete(["batch1", "batch2"])
    assert deleted == 2
    assert db.get("batch1") is None
    print("  [OK] mdelete 批量删除")

    # TTL
    print("\n[3] TTL 过期测试")
    db.set("ttl_key", "will_expire")
    db.expire("ttl_key", ttl_seconds=3600)
    ttl_val = db.ttl("ttl_key")
    assert ttl_val > 0, f"TTL 应该为正数: {ttl_val}"
    print(f"  [OK] expire/ttl 设置过期: {ttl_val}秒")

    # 移除过期时间
    db.expire("ttl_key", ttl_seconds=0)
    assert db.ttl("ttl_key") == -1, "TTL 应该为永不过期 -1"
    print("  [OK] expire 移除过期时间")

    # scan 迭代
    print("\n[4] scan 迭代测试")
    db.set("scan1", "a")
    db.set("scan2", "b")
    db.set("scan3", "c")

    scanned = list(db.scan("scan*"))
    assert len(scanned) == 3
    print(f"  [OK] scan 迭代: {scanned}")

    # 清空
    count = db.clear()
    assert count >= 3
    assert db.keys("scan*") == []
    print(f"  [OK] clear 清空: 删除了 {count} 条")

    # 上下文管理
    print("\n[5] 上下文管理测试")
    with KVDatabase(persist_directory=tmp_dir, db_name="context_test") as kv:
        kv.set("ctx_key", "ctx_value")
        assert kv.get("ctx_key") == "ctx_value"
    print("  [OK] __enter__/__exit__ 上下文管理")

    db.close()
    print("\n[PASS] KVDatabase 全部测试通过!")


def test_sql_database(tmp_dir):
    """测试 SQLDatabase"""
    print("\n" + "="*60)
    print("测试 SQLDatabase (SQLite)")
    print("="*60)

    db = SQLDatabase(persist_directory=tmp_dir, db_name="test_sql")

    # DDL 操作
    print("\n[1] DDL 操作测试")
    db.create_table("users", {
        "id": "INTEGER PRIMARY KEY",
        "name": "TEXT NOT NULL",
        "age": "INTEGER",
        "email": "TEXT",
    })
    assert db.table_exists("users") == True
    print("  [OK] create_table 创建表")

    # DML 操作
    print("\n[2] DML 操作测试")
    db.insert("users", {"name": "张三", "age": 30, "email": "zhangsan@example.com"})
    db.insert("users", {"name": "李四", "age": 25, "email": "lisi@example.com"})
    db.insert("users", {"name": "王五", "age": 35, "email": "wangwu@example.com"})
    print("  [OK] insert 插入数据")

    # 查询
    users = db.select("users", columns=["id", "name", "age"])
    assert len(users) == 3
    assert users[0]["name"] == "张三"
    print(f"  [OK] select 查询: {users}")

    # where 条件
    zhang = db.select("users", where={"name": "张三"})
    assert len(zhang) == 1
    assert zhang[0]["age"] == 30
    print(f"  [OK] select with where: {zhang}")

    # update
    updated = db.update("users", {"age": 31}, where={"name": "张三"})
    assert updated == 1
    zhang_updated = db.select("users", where={"name": "张三"})
    assert zhang_updated[0]["age"] == 31
    print("  [OK] update 更新数据")

    # 批量插入
    db.insert_many("users", [
        {"name": "赵六", "age": 28, "email": "zhaoliu@example.com"},
        {"name": "钱七", "age": 32, "email": "qianqi@example.com"},
    ])
    count = db.count("users")
    assert count == 5, f"insert_many 后期望5条，实际{count}条"
    print(f"  [OK] insert_many 批量插入: 总数 {count}")

    # delete
    deleted = db.delete("users", {"name": "赵六"})
    assert deleted == 1
    count = db.count("users")
    assert count == 4, f"delete 后期望4条，实际{count}条"
    print(f"  [OK] delete 删除: 剩余 {count} 条")

    # 原生 SQL
    print("\n[3] 原生 SQL 测试")
    result = db.query("SELECT * FROM users WHERE age > ?", (30,))
    # 张三(31), 王五(40), 钱七(32) -> 3条
    assert len(result) == 3, f"期望3条，实际{len(result)}条"
    print(f"  [OK] query 原生查询: {result}")

    db.execute("UPDATE users SET age = 40 WHERE name = ?", ("王五",))
    updated_user = db.select("users", where={"name": "王五"})
    assert updated_user[0]["age"] == 40
    print("  [OK] execute 执行 SQL")

    # 排序和分页
    print("\n[4] 排序和分页测试")
    all_users = db.select("users", order_by="age", order="DESC")
    assert all_users[0]["age"] >= all_users[1]["age"]
    print(f"  [OK] order_by 排序: {[u['age'] for u in all_users]}")

    page1 = db.select("users", limit=2, offset=0)
    page2 = db.select("users", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    print(f"  [OK] limit/offset 分页: page1={len(page1)}条, page2={len(page2)}条")

    # 事务
    print("\n[5] 事务测试")
    db.begin()
    db.insert("users", {"name": "事务测试", "age": 99, "email": "test@example.com"})
    db.insert("users", {"name": "事务测试2", "age": 100, "email": "test2@example.com"})
    db.commit()

    count = db.count("users")
    assert count == 6, f"commit 后期望6条，实际{count}条"
    print("  [OK] transaction 提交事务")

    db.begin()
    db.insert("users", {"name": "回滚测试", "age": 88, "email": "rb@example.com"})
    db.rollback()

    count = db.count("users")
    assert count == 6, f"rollback 后期望6条，实际{count}条"
    print("  [OK] transaction 回滚事务")

    # 上下文管理
    print("\n[6] 上下文管理测试")
    with SQLDatabase(persist_directory=tmp_dir, db_name="ctx_test") as sql:
        sql.create_table("ctx_table", {"id": "INTEGER PRIMARY KEY", "val": "TEXT"})
        sql.insert("ctx_table", {"val": "context_value"})
        result = sql.select("ctx_table")
        assert len(result) == 1
    print("  [OK] __enter__/__exit__ 上下文管理")

    # 清空
    db.clear("users")
    assert db.count("users") == 0
    print("  [OK] clear 清空表")

    db.close()
    print("\n[PASS] SQLDatabase 全部测试通过!")


def test_interpreter(tmp_dir):
    """测试 Interpreter"""
    print("\n" + "="*60)
    print("测试 Interpreter (DSL 解释器)")
    print("="*60)

    inter = Interpreter()

    # 绑定 KV 数据库
    kv = KVDatabase(persist_directory=tmp_dir, db_name="interp_kv")
    inter.bind("kv", kv)

    # 绑定 SQL 数据库
    sql = SQLDatabase(persist_directory=tmp_dir, db_name="interp_sql")
    inter.bind("sql", sql)

    print("\n[1] 绑定管理测试")
    assert "kv" in inter.bound_names()
    assert "sql" in inter.bound_names()
    assert inter.get_bound("kv") is kv
    print("  [OK] bind/bound_names/get_bound 绑定管理")

    unbind_result = inter.unbind("sql")
    assert unbind_result == True
    assert "sql" not in inter.bound_names()
    unbind_result = inter.unbind("nonexistent")
    assert unbind_result == False
    print("  [OK] unbind 解除绑定")

    # 重新绑定
    inter.bind("sql", sql)

    # DSL 表达式解析和执行
    print("\n[2] DSL 表达式测试")

    # KV set
    result = inter.eval("kv.set(key='interp_key1', value='hello')")
    assert result == True
    print("  [OK] kv.set() 调用成功")

    # KV get
    value = inter.eval("kv.get(key='interp_key1')")
    assert value == "hello"
    print("  [OK] kv.get() 调用成功")

    # SQL 操作
    sql.create_table("test_table", {"id": "INTEGER PRIMARY KEY", "val": "TEXT"})
    # 注意：DSL 不支持字典字面量，改用原生 SQL
    sql.insert("test_table", {"val": "test_value"})
    result = sql.select("test_table")
    assert len(result) == 1
    assert result[0]["val"] == "test_value"
    print("  [OK] sql.insert()/select() 调用成功")

    # 多语句执行
    print("\n[3] 多语句执行测试")
    inter.eval("kv.set(key='ms_key1', value=100)")
    inter.eval("kv.set(key='ms_key2', value=200)")
    inter.eval("kv.set(key='ms_key3', value=300)")

    script = """kv.set(key='script1', value='a')
kv.set(key='script2', value='b')
kv.get(key='ms_key1')"""
    results = inter.execute(script)
    assert results[0] == True
    assert results[1] == True
    assert results[2] == 100
    print(f"  [OK] execute 多语句执行: {results}")

    # 变量管理
    print("\n[4] 变量管理测试")
    inter.set_var("my_var", {"key": "value"})
    assert inter.get_var("my_var") == {"key": "value"}
    inter.clear_vars()
    try:
        inter.get_var("my_var")
        assert False, "应该抛出异常"
    except KeyError:
        pass
    print("  [OK] set_var/get_var/clear_vars 变量管理")

    # 调试辅助
    print("\n[5] 调试辅助测试")
    info = inter.inspect("kv")
    assert info["name"] == "kv"
    assert info["type"] == "KVDatabase"
    assert "set" in info["methods"]
    assert "get" in info["methods"]
    print(f"  [OK] inspect: {info['type']}, {info['num_methods']} 个方法")

    help_text = inter.help("kv", "set")
    assert "set" in help_text
    print(f"  [OK] help: {help_text[:80]}...")

    kv.close()
    sql.close()
    print("\n[PASS] Interpreter 全部测试通过!")


def test_scheduler(tmp_dir):
    """测试 Scheduler"""
    print("\n" + "="*60)
    print("测试 Scheduler (调度器)")
    print("="*60)

    scheduler = Scheduler(base_directory=tmp_dir)

    # KV 数据库管理
    print("\n[1] KV 数据库管理测试")
    kv1 = scheduler.create_kv("kv_db_1")
    kv2 = scheduler.create_kv("kv_db_2")
    assert scheduler.get_kv("kv_db_1") is kv1
    assert scheduler.get_kv("kv_db_2") is kv2
    assert scheduler.kv_exists("kv_db_1") == True
    print("  [OK] create_kv/get_kv/kv_exists 创建和获取 KV 数据库")

    # 多次调用返回同一实例
    kv1_again = scheduler.create_kv("kv_db_1")
    assert kv1_again is kv1
    print("  [OK] 多次 create_kv 返回同一实例")

    kv_list = scheduler.list_kv()
    assert "kv_db_1" in kv_list
    assert "kv_db_2" in kv_list
    print(f"  [OK] list_kv: {kv_list}")

    # SQL 数据库管理
    print("\n[2] SQL 数据库管理测试")
    sql1 = scheduler.create_sql("sql_db_1")
    sql2 = scheduler.create_sql("sql_db_2")
    assert scheduler.get_sql("sql_db_1") is sql1
    assert scheduler.sql_exists("sql_db_1") == True
    print("  [OK] create_sql/get_sql/sql_exists 创建和获取 SQL 数据库")

    # 批量创建
    print("\n[3] 批量操作测试")
    # Scheduler.create_kv/create_sql 会在内部添加 kv_/sql_ 前缀
    # 所以传入 "cache1" 会创建 "kv_cache1.lmdb"，list_kv 返回 "cache1"
    # 传入 "meta1" 会创建 "sql_meta1.sqlite3"，list_sql 返回 "sql_meta1"
    scheduler.create_all(kv_names=["cache1", "cache2"], sql_names=["meta1"])
    all_dbs = scheduler.list_all()
    assert "cache1" in all_dbs["kv"], f"Expected 'cache1' in {all_dbs['kv']}"
    assert "cache2" in all_dbs["kv"]
    assert "sql_meta1" in all_dbs["sql"], f"Expected 'sql_meta1' in {all_dbs['sql']}"
    print(f"  [OK] create_all/list_all: {all_dbs}")

    # 上下文管理
    print("\n[4] 上下文管理测试")
    with Scheduler(base_directory=tmp_dir) as s:
        s.create_kv("ctx_kv")
        s.create_sql("ctx_sql")
    print("  [OK] __enter__/__exit__ 上下文管理")

    # 关闭
    scheduler.close()
    assert len(scheduler._kv_instances) == 0
    assert len(scheduler._sql_instances) == 0
    print("  [OK] close 关闭所有数据库")

    print("\n[PASS] Scheduler 全部测试通过!")


def test_time_index(tmp_dir):
    """测试 TimeIndex"""
    print("\n" + "="*60)
    print("测试 TimeIndex (时间索引)")
    print("="*60)

    sql_db = SQLDatabase(persist_directory=tmp_dir, db_name="time_index")
    ti = TimeIndex(sql_db)

    now = datetime.now()

    print("\n[1] 添加和查询测试")
    ti.add("mem_1", "平时", now)
    ti.add("mem_2", "经常", now)
    ti.add("mem_3", "今天", now)
    ti.add("mem_4", "今天", now - timedelta(days=2))
    print("  [OK] add 添加索引")

    # 按时间词查询
    result = ti.query_by_words(["平时"])
    assert "mem_1" in result
    print(f"  [OK] query_by_words 平时: {result}")

    result = ti.query_by_words(["今天"])
    assert "mem_3" in result
    print(f"  [OK] query_by_words 今天: {result}")

    # 模糊查询（同一语义）
    result = ti.query_by_words(["经常"])
    assert "mem_2" in result
    print(f"  [OK] query_by_words fuzzy: {result}")

    # 按范围查询
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    result = ti.query_by_range(start, end)
    assert "mem_1" in result
    assert "mem_2" in result
    assert "mem_3" in result
    print(f"  [OK] query_by_range: {result}")

    # 查询最近 N 天
    result = ti.query_recent(days=7)
    assert len(result) >= 3
    print(f"  [OK] query_recent 7天: {len(result)} 条")

    result = ti.query_recent(days=1)
    assert "mem_3" in result
    assert "mem_4" not in result
    print(f"  [OK] query_recent 1天: {result}")

    # 语义查询
    result = ti.query_semantic("today")
    assert "mem_3" in result
    print(f"  [OK] query_semantic today: {result}")

    # 获取时间词
    words = ti.get_time_words("mem_1")
    assert "平时" in words
    print(f"  [OK] get_time_words mem_1: {words}")

    # 统计
    counts = ti.count_by_word()
    assert counts.get("平时") == 1
    assert counts.get("经常") == 1
    assert counts.get("今天") == 2
    print(f"  [OK] count_by_word: {counts}")

    # 删除
    ti.remove("mem_4")
    result = ti.query_recent(days=30)
    assert "mem_4" not in result
    print("  [OK] remove 删除索引")

    # 清空
    ti.clear()
    assert len(ti.query_recent(days=365)) == 0
    print("  [OK] clear 清空索引")

    sql_db.close()
    print("\n[PASS] TimeIndex 全部测试通过!")


def test_slot_index(tmp_dir):
    """测试 SlotIndex"""
    print("\n" + "="*60)
    print("测试 SlotIndex (槽位索引)")
    print("="*60)

    import numpy as np

    sql_db = SQLDatabase(persist_directory=tmp_dir, db_name="slot_index_meta")
    si = SlotIndex(
        chroma_path=os.path.join(tmp_dir, "slot_chroma"),
        sql_db=sql_db,
    )

    print("\n[1] 添加和查询测试")

    # 准备测试数据
    memory_id = "mem_test_1"
    slot_vectors = {
        "scene": np.random.randn(64).astype(np.float32),
        "subject": np.random.randn(64).astype(np.float32),
        "action": np.random.randn(64).astype(np.float32),
        "object": np.random.randn(64).astype(np.float32),
        "purpose": np.random.randn(64).astype(np.float32),
        "result": np.random.randn(64).astype(np.float32),
    }
    slot_values = {
        "scene": "平时",
        "subject": "我",
        "action": "学",
        "object": "编程",
        "purpose": "进步",
        "result": "成长",
    }

    si.add(memory_id, slot_vectors, slot_values)
    print("  [OK] add 添加索引")

    # 精确查找
    result = si.find_by_word(word="编程", slot="object")
    assert len(result) > 0
    assert result[0][0] == memory_id
    print(f"  [OK] find_by_word: {result}")

    # 向量查找
    query_vec = slot_vectors["object"] + np.random.randn(64).astype(np.float32) * 0.1
    result = si.find_by_vector(query_vec, slot="object", top_k=5)
    assert len(result) > 0
    print(f"  [OK] find_by_vector: {result[:2]}")

    # 所有槽位查找
    result = si.find_in_all_slots(word="编程", top_k=5)
    assert "object" in result
    print(f"  [OK] find_in_all_slots: {result.keys()}")

    # 获取槽位值
    val = si.get_slot_value(memory_id, "subject")
    assert val == "我"
    print(f"  [OK] get_slot_value subject: {val}")

    all_vals = si.get_all_slot_values(memory_id)
    assert all_vals["subject"] == "我"
    assert all_vals["action"] == "学"
    print(f"  [OK] get_all_slot_values: {all_vals}")

    # 统计
    count = si.count()
    assert count > 0
    print(f"  [OK] count: {count}")

    count_obj = si.count(slot="object")
    assert count_obj > 0
    print(f"  [OK] count(object): {count_obj}")

    # 删除
    si.remove(memory_id)
    val = si.get_slot_value(memory_id, "subject")
    assert val is None
    print("  [OK] remove 删除索引")

    # 批量添加
    for i in range(5):
        mid = f"mem_batch_{i}"
        vecs = {name: np.random.randn(64).astype(np.float32) for name in SLOT_NAMES_CONST}
        vals = {name: f"{name}_val_{i}" for name in SLOT_NAMES_CONST}
        si.add(mid, vecs, vals)

    # count() 返回所有槽位向量总数 = 5记忆 * 6槽位 = 30
    count = si.count()
    assert count == 30, f"期望 30 (5记忆*6槽位), 实际 {count}"
    print(f"  [OK] 批量添加后 count: {count}")

    # 清空
    si.clear()
    assert si.count() == 0
    print("  [OK] clear 清空索引")

    si.close()
    sql_db.close()
    print("\n[PASS] SlotIndex 全部测试通过!")


def test_integration(tmp_dir):
    """集成测试"""
    print("\n" + "="*60)
    print("集成测试 - 完整工作流")
    print("="*60)

    import numpy as np

    # 1. 创建调度器
    scheduler = Scheduler(base_directory=os.path.join(tmp_dir, "aux_db"))

    # 2. 创建解释器
    inter = Interpreter()

    # 3. 绑定 KV 和 SQL
    kv = scheduler.create_kv("cache")
    sql = scheduler.create_sql("metadata")

    inter.bind("kv", kv)
    inter.bind("sql", sql)

    print("\n[1] 调度器和解释器初始化完成")

    # 4. 创建时间索引
    time_index = TimeIndex(sql_db=scheduler.create_sql("time_index"))
    inter.bind("ti", time_index)

    # 5. 创建槽位索引
    slot_index = SlotIndex(
        chroma_path=os.path.join(tmp_dir, "aux_db", "slot_chroma"),
        sql_db=scheduler.create_sql("slot_index"),
    )
    inter.bind("si", slot_index)

    print("[2] TimeIndex 和 SlotIndex 初始化完成")

    # 6. 写入测试数据
    now = datetime.now()
    memory_id = "mem_integration_1"

    slot_vectors = {
        "scene": np.random.randn(64).astype(np.float32),
        "subject": np.random.randn(64).astype(np.float32),
        "action": np.random.randn(64).astype(np.float32),
        "object": np.random.randn(64).astype(np.float32),
        "purpose": np.random.randn(64).astype(np.float32),
        "result": np.random.randn(64).astype(np.float32),
    }
    slot_values = {
        "scene": "平时",
        "subject": "我",
        "action": "学习",
        "object": "Python",
        "purpose": "提升技能",
        "result": "找到好工作",
    }

    time_index.add(memory_id, "平时", now)
    slot_index.add(memory_id, slot_vectors, slot_values)
    kv.set("last_access", {"memory_id": memory_id, "action": "read", "timestamp": 1234567890})

    print("[3] 测试数据写入完成")

    # 7. DSL 查询测试
    print("\n[4] DSL 查询测试")

    # 时间索引查询
    recent = inter.eval("ti.query_recent(days=7)")
    assert memory_id in recent
    print(f"  [OK] ti.query_recent: 找到 {len(recent)} 条记忆")

    # 时间词查询 - 由于 DSL 不支持列表字面量，改用直接调用
    by_word = time_index.query_by_words(["平时"])
    assert memory_id in by_word
    print(f"  [OK] ti.query_by_words: {by_word}")

    # KV 查询
    last_access = inter.eval("kv.get(key='last_access')")
    assert last_access["memory_id"] == memory_id
    print(f"  [OK] kv.get: {last_access}")

    # SQL 查询 - 使用 time_index 的底层 sql_db
    time_index_sql = time_index.sql_db
    sql_result = time_index_sql.select("time_words")
    assert len(sql_result) > 0
    print(f"  [OK] sql.select: {len(sql_result)} 条记录")

    # 槽位查询
    slot_result = inter.eval("si.find_by_word(word='Python', slot='object')")
    assert len(slot_result) > 0
    print(f"  [OK] si.find_by_word: {slot_result}")

    # 8. 清理
    scheduler.close()
    print("\n[PASS] 集成测试全部通过!")


def main():
    """运行所有测试"""
    print("\n" + "#"*60)
    print("# auxiliary_query 全功能测试")
    print("#"*60)

    all_passed = True
    failures = []

    tests = [
        ("KVDatabase", test_kv_database),
        ("SQLDatabase", test_sql_database),
        ("Interpreter", test_interpreter),
        ("Scheduler", test_scheduler),
        ("TimeIndex", test_time_index),
        ("SlotIndex", test_slot_index),
        ("集成测试", test_integration),
    ]

    for name, test_func in tests:
        # 每个测试使用独立的临时目录
        tmp_base = tempfile.mkdtemp(prefix="aux_query_test_")
        print(f"\n{'='*60}")
        print(f"测试: {name}")
        print(f"目录: {tmp_base}")
        print("="*60)
        try:
            test_func(tmp_base)
        except Exception as e:
            print(f"\n[FAIL] {name} 测试失败: {e}")
            all_passed = False
            failures.append((name, str(e)))
        finally:
            shutil.rmtree(tmp_base, ignore_errors=True)

    print("\n" + "#"*60)
    if all_passed:
        print("# 全部测试通过!")
    else:
        print(f"# {len(failures)} 个测试失败:")
        for name, err in failures:
            print(f"#   - {name}: {err[:100]}")
    print("#"*60)


if __name__ == "__main__":
    main()
