# 项目交接文档

## 一、项目是什么

XCMemory Interest — 结构化记忆管理系统。核心是一个角色扮演 Chat 应用，角色通过与用户对话逐步建立记忆（6 槽位向量存储），可以后续召回。

**两个主要界面：**
- `start_server.py --gradio` → WebUI（管理记忆、MQL 查询、NL 查询）
- `chat/main.py --character example` → 终端 Chat（角色对话 + 记忆写入）

---

## 二、如何启动

```powershell
# 两个终端分别跑

# 终端1：记忆服务器（必须先启动）
o:/project/xcmemory_interest/venv/Scripts/python.exe o:/project/xcmemory_interest/start_server.py --gradio

# 终端2：Chat
o:/project/xcmemory_interest/venv/Scripts/python.exe o:/project/xcmemory_interest/chat/main.py --character example
```

WebUI 在 `http://127.0.0.1:7860`，API 在 `http://127.0.0.1:8080`。

---

## 三、六槽位（最核心的概念）

所有记忆都存储为 6 个槽位：

```
<scene><subject><action><object><purpose><result>
```

| 槽位 | 含义 | 示例 |
|------|------|------|
| scene | 时间/空间场景 | `<所有>` `<平时>` `<家里>` `<2026-04-27>` |
| subject | 主体 | `<星织>` |
| action | 关系动词（19个预定义） | `<是>` `<有>` `<的>` `<叫>` `<差>` `<喜欢>` `<知道>` |
| object | 关联对象 | `<哥哥>` `<名字>` `<绯绯>` |
| purpose | **语义类别**（回答什么类型的问题） | `<名字>` `<身份>` `<关系>` `<年龄差距>` `<喜好>` |
| result | **具体值**（purpose 的答案） | `<星织>` `<旅行者>` `<一岁>` `<火锅>` |

### 核心规则

- **每个槽位只写一个词，不写短句**
- purpose = 问什么（语义类别），result = 答什么（具体值）
- 缺槽用 `<无>` 占位，六槽必须等长

### 正确示例

```
"星织的名字是星织" → <所有><星织><的><名字><名字><星织>
"星织有个哥哥叫绯绯" → <所有><星织><有><哥哥><关系><绯绯>
"星织和绯绯只差一岁" → <所有><星织><差><绯绯><年龄差距><一岁>
"星织是旅行者" → <平时><星织><是><旅行者><身份><旅行者>
"星织喜欢火锅" → <平时><星织><喜欢><火锅><喜好><火锅>
```

### 错误示例

```
❌ <无><星织><是><星织和绯绯只差一岁><无><无>  ← 短句在 object，purpose/result 全是无
❌ <平时><星织><被父亲交给哥哥照顾><无><发展><慢慢来>  ← 短句，动词不在预定义列表
```

相关代码：
- `nl/write_mql_generator.py` — prompt + 硬兜底 + 验证修正
- `nl/slot_extractor.py` — 从自由文本提取六槽
- `nl/intent_classifier.py` — 意图识别中的六槽描述
- `docs/MQL规范.md` — action 表、purpose 词表
- `docs/MEMU_TEXT2MEM_REFERENCE.md` — 完整参考

---

## 四、NL Pipeline 写入流程

```
用户说"记住: 我是星织，有个哥哥叫绯绯"
        ↓
  ① IntentClassifier（LLM 调用）
     分类意图 → writes=["星织的名字是星织","星织有个哥哥叫绯绯"]
     lifecycle=long
        ↓
  ②a WriteMQLGenerator（LLM 调用）
     陈述句 → INSERT MQL
     四级 fallback：XML标签 → 裸INSERT → 纯文本 → 硬兜底(不调LLM)
        ↓
  ②b _dedup_writes()
     解析每条 INSERT 的六槽 → search_subspace 查询已有记忆
     distance < 0.15 → 跳过（重复）
        ↓
  ③ Interpreter.execute_script()
     执行去重后的 MQL → 写入 SQLite + ChromaDB + 索引
```

### LLM 调用次数

每个「记住」触发：IntentClassifier(1次) + WriteMQLGenerator(1次) = **2 次服务器 LLM 调用**。  
Chat 自身的对话生成另算 1 次。

### 关键文件

| 文件 | 作用 |
|------|------|
| `nl/pipeline.py` | 写入流程编排 + 去重 |
| `nl/intent_classifier.py` | 意图识别（writes/queries/lifecycle） |
| `nl/write_mql_generator.py` | 陈述句→INSERT MQL（多级 fallback + 验证） |
| `nl/mql_generator.py` | NL→SELECT MQL（查询路径） |
| `netapi/__init__.py:728` | `POST /api/v1/nl-query` 入口 |

### 意图识别提示词要点

位置：`nl/intent_classifier.py` 的 `INTENT_CLASSIFY_PROMPT`

- 「记住:」前缀 → 写入
- 不以问号结尾的叙述句 → 默认写入（泛化写入）
- 不确定时 → 优先判为写入
- 元评论（"我需要确认一下"）→ 过滤掉，不拆为写入句
- 信息原子化：一句一个事实，拆成多条
- `max_tokens=1024`（之前 512 太小会截断输出）

### WriteMQLGenerator 提示词要点

位置：`nl/write_mql_generator.py` 的 `WRITE_MQL_PROMPT`

- 详细定义了六槽语义（purpose=类别，result=值）
- 19 个预定义 action 动词
- 6 个完整范例（带解读注释）
- 输出格式：`<mql>INSERT ...;INSERT ...</mql>`
- 硬兜底：LLM 失败时直接从 statements 构建 INSERT（`_infer_purpose` 启发式推断 purpose）

---

## 五、Chat 应用架构

```
chat/
├── main.py              # 入口：加载配置→角色卡→客户端→引擎→UI
├── config.toml          # 用户身份、记忆API、LLM（deepseek）、自白设置
├── character_card.py    # YAML 角色卡加载（name/consciousness/system_name等）
├── chat_engine.py       # 核心引擎：自白流程+记忆触发+引导模式
├── memory_client.py     # 星辰记忆 HTTP 客户端（nl_query/mql_query/count）
├── llm_client.py        # OpenAI 兼容流式客户端（120s timeout）
├── requirements.txt     # openai/httpx/pyyaml/tomli/rich
├── characters/
│   └── example.yaml     # 星织角色卡（system_name: xingzhi）
└── ui/
    └── terminal_ui.py   # Rich 终端 UI
```

### 对话流程

```
用户输入
  → 轻度记忆检索（top 3，注入 system prompt）
  → LLM 流式生成
  → 解析 <monologue> 标签：
      ├─ 按换行分段
      ├─ 检测「记住」→ 调用记忆 API 写入
      ├─ 检测「回忆一下」→ 调用记忆 API 查询
  → </monologue> 之后的所有文本 = 正式回复（不需要标签）
```

### 输出格式

```
<monologue>
记住我是18岁的女性
记住我的性格是理论探索者
对方在帮我建立自我认知。
</monologue>
嗯，记住了。听起来我确实不太像个普通的18岁女孩。
```

- 自白用 `<monologue>...</monologue>` 包裹
- 回复直接写在后面，**不需要任何标签**
- 「记住」「回忆一下」只能出现在自白中

### 引导模式

启动时 `count_memories() == 0` → 自动进入引导模式
- 使用独立 system prompt（强调主动「记住」写入记忆）
- 用户输入 `/done` 结束引导
- Chat 自动检测记忆数量决定模式

### 关键设计决策

- `system_name` 在角色卡 YAML 中定义，不在 config.toml
- 回复兜底扫描：误将「记住」写在回复中也会被捕获
- `_is_meaningful_write()` 过滤假阳性（"嗯，记住了。" → 忽略）
- LLM timeout 120 秒（之前无超时会永久挂起）

---

## 六、LLM 模型

**当前配置**（`config.toml` + `chat/config.toml`）：
```
model = 'deepseek-v4-flash'
base_url = 'https://api.deepseek.com'
api_key = 'sk-12b183cdc66147a2abd8bab082239b1c'
```

**已知问题**：`deepseek-v4-flash` 在 temperature > 0.6 时需要 ≥100 max_tokens 才能正常启动。Chat 用 2048，没问题。

**备用方案**：直接改成 `deepseek-chat` 也完全可用。

---

## 七、数据库 Schema

三个 SQLite 表（每个记忆系统独立一套），所有表都是自动创建 + 旧表自动迁移：

| 表 | 位置 | 关键列 |
|----|------|--------|
| `memories` | `vec_db_crud.py:209` | id, query_sentence, query_embedding(blob), extra |
| `slot_value_index` | `vec_db_crud.py:230` | memory_id, scene_value, subject_value, ... |
| `slot_metadata` | `slot_index.py:74` | memory_id, slot_scene, slot_subject, ... |

**迁移机制**：`_init_kv_db()` / `_init_tables()` 中 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`（try/except 幂等）。

ChromaDB 向量数据与 SQLite 在同一目录下。`DELETE FROM memories` 会同时清理 SQLite + ChromaDB + 所有索引。

---

## 八、常用 MQL

```sql
-- 查询
SELECT * FROM memories WHERE subject='星织' LIMIT 10
SELECT * FROM memories WHERE [subject='星织'] SEARCH TOPK 5
SELECT * FROM memories WHERE subject='星织' TIME year(2026) AND month(04)

-- 写入
INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', 2592000)

-- 删除
DELETE FROM memories                    -- 清空当前系统所有记忆
DELETE FROM memories WHERE subject='星织'
```

WebUI → 📝 MQL 查询 标签页可直接执行。

---

## 九、已知问题 & 注意事项

1. **必须用 venv 的 Python**（`venv/Scripts/python.exe`），系统 Python 的 torch DLL 加载失败
2. **服务器修改代码后必须重启**，否则 prompt/fallback 不生效
3. **`config.toml` 的 `database_root` 必须用绝对路径**
4. **旧数据库可能存在 schema 不兼容**（time→scene 改名）。最干净的方式：`DELETE FROM memories` 清空，或直接删 `data/` 目录重建
5. **HybridSearch 测试会失败**（`'coroutine' object is not iterable`，已有 bug，非此次引入）
6. **重复记忆**已有去重机制（`_dedup_writes`，distance < 0.15），但阈值可能需要根据实际情况调整
7. **LLM 偶尔忘记输出自白** — prompt 已多次强化"不写自白 = 放弃记忆"
8. **Chat 回复偶尔缺失** — 已加降级处理（buffer 残留文本作为回复）

---

## 十、本会话主要改动（未提交的 git diff）

上次提交 `4842bdf` 之后改动：

| 文件 | 改动 |
|------|------|
| `chat/chat_engine.py` | 输出格式简化：去掉 `<reply>` 标签，`</monologue>` 后直接是回复；流式解析重写；LLM 超时 120s；假阳性过滤；泛化写入 prompt |
| `nl/intent_classifier.py` | max_tokens 512→1024；泛化写入规则；元评论过滤；优先写入 |
| `nl/write_mql_generator.py` | 硬兜底不再用 `<无><无>`；`_validate_and_fix_slots` 后处理验证；`_infer_purpose` 启发式；action 列表扩展到 19 个 |
| `nl/pipeline.py` | `_dedup_writes` 去重（distance < 0.15） |
| `nl/slot_extractor.py` | 六槽语义更新；action 扩展；词不短句规则 |
| `nl/mql_generator.py` | 六槽定义更新 |
| `chat/llm_client.py` | 加 httpx timeout |
| `basic_crud/vec_db_crud.py` | schema 迁移：extra 列、scene_value 列 |
| `auxiliary_query/indexes/slot_index.py` | schema 迁移：slot_scene 列 |
| `netapi/__init__.py` | API 响应增加 steps_summary + trace |
| `webui/app.py` | MQL 示例更新 |
| `docs/MQL规范.md` | action 表、purpose 词表、示例全部更新 |
| `docs/MEMU_TEXT2MEM_REFERENCE.md` | 同上 |
| `chat/config.toml` + `config.toml` | LLM model 切换 |

---

## 十一、测试

```powershell
# 全部 NL 模块测试
o:/project/xcmemory_interest/venv/Scripts/python.exe -m pytest tests/test_nl_modules.py -v

# 跳过已知失败的 HybridSearch 测试
o:/project/xcmemory_interest/venv/Scripts/python.exe -m pytest tests/test_nl_modules.py -v -k "not HybridSearch"
```

46/47 通过（1 个 HybridSearch 已有 bug）。
