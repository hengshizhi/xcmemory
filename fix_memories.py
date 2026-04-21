# -*- coding: utf-8 -*-
"""
记忆内容批量修复脚本

用法：
    o:/project/xcmemory_interest/venv/Scripts/python.exe fix_memories.py

修复内容：
1. 将所有 content 和 query_sentence 中的 "助手" 替换为 "星织"
2. 修正单条指定记忆的内容
"""
import sys
import sqlite3
import json
import shutil
from pathlib import Path

# ====== 配置 ======
SYSTEM_NAME = "xingzhi1"  # 实际活跃系统
DATA_ROOT = Path(r"O:\project\xcmemory_interest\data\xcmemory") / SYSTEM_NAME
SQLITE_PATH = DATA_ROOT / "vec_db" / "kv" / "memory.db"
CHROMA_PATH = DATA_ROOT / "vec_db" / "chroma_data" / "chroma.sqlite3"
SLOT_CHROMA_PATH = DATA_ROOT / "slot_chroma" / "chroma.sqlite3"

# ====== 单条修正 ======
# mem_a8b88031d083: "助手确认用户视其为妹妹" → "用户确认助手（星织）为妹妹"
SPECIFIC_FIXES = {
    "mem_a8b88031d083": {
        "content": "用户确认助手（星织）为妹妹",
        "query_sentence": "用户确认助手（星织）为妹妹",
    }
}

# ====== 通用替换 ======
REPLACEMENTS = [
    ("助手", "星织"),
]


def backup_db(db_path: Path, suffix: str = ".bak"):
    """备份数据库"""
    if db_path.exists():
        bak = db_path.with_suffix(db_path.suffix + suffix)
        shutil.copy2(db_path, bak)
        print(f"  [OK] backup: {bak.name}")
        return bak
    return None


def get_memories(conn: sqlite3.Connection):
    """读取所有记忆"""
    cur = conn.cursor()
    cur.execute("SELECT id, content, query_sentence FROM memories")
    return cur.fetchall()


def main():
    print("=" * 60)
    print("Memory Content Fix Script")
    print("=" * 60)

    # 备份
    print("\n[B] 备份数据库...")
    backup_db(SQLITE_PATH)
    backup_db(CHROMA_PATH)
    backup_db(SLOT_CHROMA_PATH)

    # 连接 SQLite
    print(f"\n[R] 读取 SQLite: {SQLITE_PATH}")
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.text_factory = str
    cur = conn.cursor()

    # 读取所有记忆
    memories = get_memories(conn)
    print(f"   共有 {len(memories)} 条记忆")

    # 统计
    total_changes = 0
    specific_changes = 0

    # 1. 应用单条修正
    print("\n[C] 修正指定记忆...")
    for mid, content, query_sentence in memories:
        if mid in SPECIFIC_FIXES:
            fix = SPECIFIC_FIXES[mid]
            print(f"   [{mid}]")
            print(f"     content: {content[:50]}... -> {fix['content'][:50]}...")
            print(f"     query:   {query_sentence[:50]}... -> {fix['query_sentence'][:50]}...")
            cur.execute(
                "UPDATE memories SET content=?, query_sentence=?, updated_at=datetime('now') WHERE id=?",
                (fix["content"], fix["query_sentence"], mid),
            )
            specific_changes += 1
            total_changes += 1

    # 2. 应用通用替换
    print("\n[R] 通用替换（助手->星织）...")
    for old, new in REPLACEMENTS:
        cur.execute("SELECT COUNT(*) FROM memories WHERE content LIKE ? OR query_sentence LIKE ?",
                    (f"%{old}%", f"%{old}%"))
        count = cur.fetchone()[0]
        print(f"   包含 '{old}' 的记忆: {count} 条")

    # 执行替换
    for old, new in REPLACEMENTS:
        cur.execute("""
            UPDATE memories
            SET content = REPLACE(content, ?, ?),
                updated_at = datetime('now')
            WHERE content LIKE ?
        """, (old, new, f"%{old}%"))
        content_updated = cur.rowcount

        cur.execute("""
            UPDATE memories
            SET query_sentence = REPLACE(query_sentence, ?, ?),
                updated_at = datetime('now')
            WHERE query_sentence LIKE ?
        """, (old, new, f"%{old}%"))
        query_updated = cur.rowcount

        print(f"   替换 '{old}' -> '{new}': content更新{content_updated}条, query更新{query_updated}条")
        total_changes += content_updated + query_updated

    conn.commit()

    # 验证
    print("\n[V] 验证替换结果（助手）...")
    cur.execute("SELECT COUNT(*) FROM memories WHERE content LIKE ? OR query_sentence LIKE ?",
                (f"%助手%", f"%助手%"))
    remaining = cur.fetchone()[0]
    print(f"   剩余包含'助手'的记忆: {remaining} 条")

    # 展示修改后的内容
    print("\n[L] 修改后的记忆（前10条）:")
    cur.execute("SELECT id, substr(content,1,60) FROM memories LIMIT 10")
    for row in cur.fetchall():
        print(f"   {row[0]}: {row[1]}")

    conn.close()
    print(f"\n[DONE] 完成！共修改 {total_changes} 处（含 {specific_changes} 条指定修正）")
    print("\n[WARN] SQLite 已更新。ChromaDB 向量仍基于旧内容编码。")
    print("   text search 不受影响（不走向量）；向量检索下次导入记忆时自动修复。")


if __name__ == "__main__":
    main()
