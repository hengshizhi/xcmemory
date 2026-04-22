"""
星尘记忆系统 - Gradio WebUI
"""

import os
import sys
from pathlib import Path
from typing import Optional

import gradio as gr
import pandas as pd

# ============================================================================
# 全局状态
# ============================================================================

_api_server: Optional["APIServer"] = None
_auth_context = None
_llm_client = None
_llm_model = "xiaomi/mimo-v2-flash"
_debug_mode = False
_is_admin = False
_active_system_name = None

SLOT_NAMES = ["time", "subject", "action", "object", "purpose", "result"]
TABLE_HEADERS = ["ID", "time", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "创建时间", "更新时间"]
SEARCH_HEADERS = ["ID", "time", "subject", "action", "object", "purpose", "result", "lifecycle", "距离", "匹配槽位"]
MQL_HEADERS = ["ID", "time", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "创建时间"]

# ============================================================================
# 初始化（由 start_server.py 调用）
# ============================================================================

def init_webui(api_server: "APIServer", auth_username: str, is_admin: bool, openai_config: dict = None, debug: bool = False):
    global _api_server, _auth_context, _is_admin, _active_system_name, _llm_client, _llm_model, _debug_mode
    _api_server = api_server
    _is_admin = is_admin

    from xcmemory_interest.user_manager import AuthContext, PermissionType
    _auth_context = AuthContext(
        username=auth_username,
        is_superadmin=is_admin,
        llm_enabled=is_admin,
        permissions={"*": [PermissionType.ADMIN]} if is_admin else {},
    )

    # 设置激活系统
    current_name = _api_server.pyapi.active_system_name
    if current_name:
        _active_system_name = current_name
    else:
        systems = _api_server.pyapi.list_all_systems()
        if systems:
            first = systems[0]["name"]
            _api_server.pyapi.set_active_system(first)
            _active_system_name = first

    # LLM 客户端
    _llm_client = None
    if openai_config and openai_config.get("api_key"):
        try:
            from openai import AsyncOpenAI
            _llm_client = AsyncOpenAI(
                api_key=openai_config["api_key"],
                base_url=openai_config.get("base_url", "https://openrouter.ai/api/v1"),
            )
            _llm_model = openai_config.get("model", "xiaomi/mimo-v2-flash")
        except Exception as e:
            print(f"[WARN] LLM 客户端初始化失败: {e}")

    _debug_mode = debug


# ============================================================================
# 工具函数
# ============================================================================

def _parse_sentence_parts(sentence: str):
    parts, current, in_bracket = [], "", False
    for ch in sentence:
        if ch == "<":
            in_bracket, current = True, ""
        elif ch == ">":
            in_bracket = False
            parts.append(current)
        elif in_bracket:
            current += ch
    if len(parts) >= 6:
        return dict(zip(SLOT_NAMES, parts[:6]))
    return {name: "" for name in SLOT_NAMES}


def _build_df(rows_data, headers):
    if not rows_data:
        return pd.DataFrame(columns=headers)
    rows = []
    for row in rows_data:
        if isinstance(row, dict):
            parts = _parse_sentence_parts(row.get("query_sentence", ""))
            content_val = str(row.get("content", "") or "")[:80]
            rows.append({
                "ID": str(row.get("id", "")),
                "time": parts.get("time", ""),
                "subject": parts.get("subject", ""),
                "action": parts.get("action", ""),
                "object": parts.get("object", ""),
                "purpose": parts.get("purpose", ""),
                "result": parts.get("result", ""),
                "内容(前80字)": content_val + ("..." if len(str(row.get("content", "") or "")) > 80 else ""),
                "lifecycle": str(row.get("lifecycle", "")),
                "创建时间": str(row.get("created_at", ""))[:16] if row.get("created_at") else "",
                "更新时间": str(row.get("updated_at", ""))[:16] if row.get("updated_at") else "",
            })
    return pd.DataFrame(rows, columns=headers)


def _build_search_df(rows_data):
    if not rows_data:
        return pd.DataFrame(columns=SEARCH_HEADERS)
    rows = []
    for row in rows_data:
        if isinstance(row, dict):
            parts = _parse_sentence_parts(row.get("query_sentence", ""))
            content_val = str(row.get("content", "") or "")[:80]
            rows.append({
                "ID": str(row.get("id", "")),
                "time": parts.get("time", ""),
                "subject": parts.get("subject", ""),
                "action": parts.get("action", ""),
                "object": parts.get("object", ""),
                "purpose": parts.get("purpose", ""),
                "result": parts.get("result", ""),
                "lifecycle": str(row.get("lifecycle", "")),
                "距离": f"{row.get('distance', 0):.4f}" if row.get("distance") is not None else "",
                "匹配槽位": row.get("match_count", ""),
            })
    return pd.DataFrame(rows, columns=SEARCH_HEADERS)


def _build_mql_df(rows_data):
    if not rows_data:
        return pd.DataFrame(columns=MQL_HEADERS)
    rows = []
    for row in rows_data:
        if isinstance(row, dict):
            parts = _parse_sentence_parts(row.get("query_sentence", ""))
            content_val = str(row.get("content", "") or "")
            rows.append({
                "ID": str(row.get("id", ""))[:16],
                "time": parts.get("time", ""),
                "subject": parts.get("subject", ""),
                "action": parts.get("action", ""),
                "object": parts.get("object", ""),
                "purpose": parts.get("purpose", ""),
                "result": parts.get("result", ""),
                "内容(前80字)": content_val[:80] + ("..." if len(content_val) > 80 else ""),
                "lifecycle": str(row.get("lifecycle", "")),
                "创建时间": str(row.get("created_at", ""))[:16] if row.get("created_at") else "",
            })
    return pd.DataFrame(rows, columns=MQL_HEADERS)


def _exec_mql(mql: str):
    global _api_server, _auth_context
    if _api_server is None:
        return None, "❌ 未连接服务器"
    try:
        from xcmemory_interest.mql.interpreter_extended import InterpreterExtended
        interpreter = InterpreterExtended()
        interpreter.bind("api", _api_server.pyapi)
        interpreter.set_auth_context(_auth_context)
        interpreter.bind("um", _api_server.user_manager)
        interpreter.bind("mem", _api_server.pyapi.active_system)
        result = interpreter.execute(mql)
        return result, None
    except Exception as e:
        import traceback
        return None, f"❌ 执行失败: {str(e)}\n{traceback.format_exc()}"


# ============================================================================
# 记忆表
# ============================================================================

def do_refresh():
    if _api_server is None:
        return pd.DataFrame(columns=TABLE_HEADERS), "❌ _api_server 未初始化"
    if _api_server.pyapi is None:
        return pd.DataFrame(columns=TABLE_HEADERS), "❌ pyapi 未初始化"
    if _api_server.pyapi.active_system is None:
        return pd.DataFrame(columns=TABLE_HEADERS), f"❌ 无活跃系统 (active_system_name={_api_server.pyapi.active_system_name})"
    sys_name = _api_server.pyapi.active_system.name
    try:
        result, err = _exec_mql("SELECT * FROM memories ORDER BY created_at DESC LIMIT 200")
        if err:
            return pd.DataFrame(columns=TABLE_HEADERS), f"[{sys_name}] {err}"
        data = result.data if result.data else []
        df = _build_df(data, TABLE_HEADERS)
        return df, f"✅ [{sys_name}] 刷新成功 ({len(data)} 条)"
    except Exception as e:
        import traceback
        return pd.DataFrame(columns=TABLE_HEADERS), f"❌ [{sys_name}] 刷新异常: {e}\n{traceback.format_exc()}"


def do_add(time_v, subject_v, action_v, object_v, purpose_v, result_v, content, lifecycle):
    if not content.strip():
        return pd.DataFrame(), "❌ 内容不能为空"
    qs = f"<{time_v}><{subject_v}><{action_v}><{object_v}><{purpose_v}><{result_v}>"
    result, err = _exec_mql(
        f"INSERT INTO memories VALUES ('{qs}', '{content.strip()}', {int(lifecycle)})"
    )
    if err:
        return pd.DataFrame(), err
    return do_refresh()


def do_delete(mid):
    if not mid:
        return pd.DataFrame(), "❌ 请填写记忆ID"
    result, err = _exec_mql(f"DELETE FROM memories WHERE id = '{mid}'")
    if err:
        return pd.DataFrame(), err
    return do_refresh()


# ============================================================================
# 向量搜索
# ============================================================================

def do_subspace_search(time_v, subject_v, action_v, object_v, purpose_v, result_v, top_k):
    qs = {k: v for k, v in {
        "time": time_v, "subject": subject_v, "action": action_v,
        "object": object_v, "purpose": purpose_v, "result": result_v
    }.items() if v}
    if not qs:
        return pd.DataFrame(columns=SEARCH_HEADERS)
    where_clause = ",".join([f"{k}='{v}'" for k, v in qs.items()])
    result, err = _exec_mql(
        f"SELECT * FROM memories WHERE [{where_clause}] SEARCH TOPK {int(top_k)}"
    )
    if err:
        return pd.DataFrame(columns=SEARCH_HEADERS)
    return _build_search_df(result.data if result.data else [])


def do_fullspace_search(time_v, subject_v, action_v, object_v, purpose_v, result_v, top_k):
    qs = {k: v for k, v in {
        "time": time_v, "subject": subject_v, "action": action_v,
        "object": object_v, "purpose": purpose_v, "result": result_v
    }.items() if v}
    if not qs:
        return pd.DataFrame(columns=SEARCH_HEADERS)
    where_clause = ",".join([f"{k}='{v}'" for k, v in qs.items()])
    result, err = _exec_mql(
        f"SELECT * FROM memories WHERE [{where_clause}] SEARCH TOPK {int(top_k)} FULLSPACE"
    )
    if err:
        return pd.DataFrame(columns=SEARCH_HEADERS)
    return _build_search_df(result.data if result.data else [])


# ============================================================================
# MQL查询
# ============================================================================

def do_mql(mql_script):
    if not mql_script.strip():
        return None, "❌ 请输入 MQL 语句"

    try:
        from xcmemory_interest.mql.interpreter_extended import InterpreterExtended
        interpreter = InterpreterExtended()
        interpreter.bind("api", _api_server.pyapi)
        interpreter.set_auth_context(_auth_context)
        interpreter.bind("um", _api_server.user_manager)
        interpreter.bind("mem", _api_server.pyapi.active_system)

        if ";" in mql_script.strip():
            results = interpreter.execute_script(mql_script.strip())
            total = sum(r.affected_rows or 0 for r in results)
            first_with_data = next((r for r in results if r.data), None)
            if first_with_data:
                return _build_mql_df(first_with_data.data), f"✅ {len(results)} 条语句，合计影响 {total} 行"
            return None, f"✅ {len(results)} 条语句，合计影响 {total} 行"
        else:
            result = interpreter.execute(mql_script.strip())
            rows = result.data if result.data is not None else []
            affected = result.affected_rows if result.affected_rows is not None else len(rows)
            msg = result.message or ""

            if rows:
                status = f"📊 {result.type or '查询'}，影响 {affected} 行"
                if msg and msg not in ("OK", "Success", ""):
                    status += f" | {msg}"
                return _build_mql_df(rows), status
            else:
                if msg:
                    return None, f"📊 {affected} 行 | {msg}"
                return None, f"📊 {affected} 行"

    except Exception as e:
        import traceback
        return None, f"❌ 执行失败: {str(e)}\n{traceback.format_exc()}"


# ============================================================================
# 自然语言查询
# ============================================================================

def do_nl_query(nl_query: str, top_k: int):
    if not nl_query.strip():
        return "❌ 请输入自然语言查询"
    if _api_server is None or _api_server.pyapi.active_system is None:
        return "❌ 未连接服务器或未选择记忆系统"
    if _llm_client is None:
        return "❌ 未配置 OpenAI API Key，无法使用自然语言查询"
    if _auth_context is not None and not _auth_context.llm_enabled:
        return "❌ 当前用户没有 LLM 查询权限，请联系管理员开启"

    import asyncio

    async def _run():
        from xcmemory_interest.nl.pipeline import NLSearchPipeline
        pipeline = NLSearchPipeline(
            llm_client=_llm_client,
            memory_system=_api_server.pyapi.active_system,
            model=_llm_model,
            debug=_debug_mode,
        )
        result = await pipeline.run(nl_query=nl_query.strip(), history=[], top_k=int(top_k))
        return result

    try:
        result = asyncio.run(_run())
        lines = [f"🤖 自然语言查询: {nl_query}"]
        items = result.get("result", [])
        lines.append(f"📊 检索到 {len(items)} 条记忆")

        llm_calls = result.get("llm_calls", 0)
        retry_count = result.get("retry_count", 0)
        if retry_count > 0:
            lines.append(f"🔄 LLM 调用 {llm_calls} 次（含重查 {retry_count} 次）")
        else:
            lines.append(f"🔄 LLM 调用 {llm_calls} 次")

        steps = result.get("steps_summary", [])
        if steps:
            for s in steps:
                lines.append(f"   {s}")

        lines.append("")
        if result.get("mql"):
            lines.append(f"📝 生成 MQL:\n   {result['mql']}")
            lines.append("")
        nl_resp = result.get("response", "")
        if nl_resp:
            lines.append(f"💬 {nl_resp}")
            lines.append("")
        if items:
            lines.append("📄 检索结果:")
            for i, item in enumerate(items, 1):
                content = item.get("content", "") or item.get("query_sentence", "")
                mid = item.get("id", item.get("memory_id", ""))
                lifecycle = item.get("lifecycle", "")
                created = str(item.get("created_at", ""))[:16]
                distance = item.get("distance", None)
                detail = f"id={mid}" + (f",lifecycle={lifecycle}" if lifecycle else "") + (f",创建={created}" if created else "") + (f",距离={distance:.4f}" if distance is not None else "")
                lines.append(f"  {i}. {content[:80]}")
                lines.append(f"     [{detail}]")
        else:
            lines.append("📄 未检索到相关记忆")

        return "\n".join(lines)
    except Exception as e:
        import traceback
        return f"❌ 查询失败: {str(e)}\n{traceback.format_exc()}"


# ============================================================================
# 系统管理（管理员）
# ============================================================================

def do_create_system(name):
    """创建新记忆系统"""
    if not name.strip():
        return "❌ 系统名称不能为空"
    result, err = _exec_mql(f"CREATE DATABASE {name.strip()}")
    if err:
        return err
    return f"✅ 系统 '{name}' 已创建"


def do_switch_system(system_name):
    """切换活跃记忆系统"""
    global _active_system_name
    if _api_server is None or not system_name:
        return pd.DataFrame(columns=TABLE_HEADERS), "❌ 未选择系统", gr.update()

    try:
        _api_server.pyapi.set_active_system(system_name)
        _active_system_name = system_name
    except Exception as e:
        return pd.DataFrame(columns=TABLE_HEADERS), f"❌ 切换失败: {e}", gr.update()

    all_names = [s["name"] for s in _api_server.pyapi.list_all_systems()]
    updated_dropdown = gr.update(choices=all_names, value=system_name)

    # 刷新记忆列表
    try:
        result, err = _exec_mql("SELECT * FROM memories ORDER BY created_at DESC LIMIT 200")
        if err:
            return pd.DataFrame(columns=TABLE_HEADERS), f"[{system_name}] {err}", updated_dropdown
        data = result.data if result.data else []
        df = _build_df(data, TABLE_HEADERS)
        return df, f"✅ [{system_name}] 已切换，{len(data)} 条记忆", updated_dropdown
    except Exception as e:
        import traceback
        return pd.DataFrame(columns=TABLE_HEADERS), f"❌ [{system_name}] {e}", updated_dropdown


def do_set_holder(holder_name):
    if _api_server is None or not _active_system_name:
        return "❌ 未选择系统"
    if not holder_name.strip():
        return "❌ 持有者名称不能为空"
    import json, base64
    credentials = base64.b64encode(b"admin:admin").decode()
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{_api_server.port}/api/v1/systems/{_active_system_name}/holder",
            data=json.dumps({"holder": holder_name.strip()}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {credentials}",
            },
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return f"✅ {data.get('message', '设置成功')}"
    except Exception as e:
        return f"❌ 设置失败: {e}"


def do_get_holder():
    if _api_server is None or _api_server.pyapi.active_system is None:
        return ""
    return getattr(_api_server.pyapi.active_system, "holder", "我")


def do_reset_llm_stats():
    sys.path.insert(0, "o:/project/xcmemory_interest/src")
    from xcmemory_interest.nl.pipeline import reset_llm_stats
    reset_llm_stats()
    return "✅ LLM 计数器已重置"


def do_get_llm_stats_text():
    sys.path.insert(0, "o:/project/xcmemory_interest/src")
    from xcmemory_interest.nl.pipeline import get_llm_stats
    s = get_llm_stats()
    total = s["total_calls"]
    queries = s["query_count"]
    avg = total / queries if queries > 0 else 0
    avg_str = f"{avg:.2f}" if queries > 0 else "N/A"
    return f"累计 {total} 次 LLM 调用 / {queries} 次查询，平均 {avg_str} 次/查询"


# ============================================================================
# 构建 Gradio UI
# ============================================================================

_CSS = """
.success-box {background: #d4edda; border: 1px solid #c3e6cb; border-radius: 8px; padding: 12px; margin: 8px 0;}
.error-box {background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 12px; margin: 8px 0;}
"""


def build_app(pre_auth: bool = False, admin_user: str = "admin"):
    # 初始化时从 PyAPI 取完整的系统列表
    _all_system_names = []
    _init_value = None
    if _api_server and _api_server.pyapi:
        _all_system_names = [s["name"] for s in _api_server.pyapi.list_all_systems()]
        _init_value = _active_system_name or (_all_system_names[0] if _all_system_names else None)

    with gr.Blocks(title="星尘记忆系统", css=_CSS, theme=gr.themes.Soft()) as app:

        gr.Markdown("## 🌟 星尘记忆系统")

        # ---- 顶部：系统切换 ----
        with gr.Row():
            system_sel = gr.Dropdown(
                label="记忆系统",
                choices=_all_system_names,
                value=_init_value,
                scale=4,
            )
            switch_btn = gr.Button("🔄 切换系统", scale=1)
            refresh_btn = gr.Button("🔃 刷新列表", scale=1)
        status_out = gr.Textbox(label="状态", lines=1, interactive=False)

        # ---------- Tab 1: 记忆管理 ----------
        with gr.Tab("🗃 记忆管理"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("**添加记忆**")
                    time_in = gr.Textbox(label="time", placeholder="平时")
                    subject_in = gr.Textbox(label="subject", placeholder="我")
                    action_in = gr.Textbox(label="action", placeholder="学习")
                    object_in = gr.Textbox(label="object", placeholder="编程")
                    purpose_in = gr.Textbox(label="purpose", placeholder="成长")
                    result_in = gr.Textbox(label="result", placeholder="有收获")
                    content_in = gr.Textbox(label="内容", placeholder="记忆内容...")
                    lifecycle_in = gr.Number(label="lifecycle(秒)", value=86400)
                    add_btn = gr.Button("➕ 添加", variant="primary")

                with gr.Column(scale=2):
                    gr.Markdown("**记忆列表**")
                    table_out = gr.DataFrame(headers=TABLE_HEADERS, label="记忆列表", max_height=400)
                    with gr.Row():
                        mid_del = gr.Textbox(label="记忆ID (删除)", scale=2)
                        del_btn = gr.Button("🗑 删除", variant="stop")

        # ---------- Tab 2: 向量搜索 ----------
        with gr.Tab("🔍 向量搜索"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("**子空间搜索**")
                    ss_time = gr.Textbox(label="time")
                    ss_subj = gr.Textbox(label="subject")
                    ss_act = gr.Textbox(label="action")
                    ss_obj = gr.Textbox(label="object")
                    ss_purp = gr.Textbox(label="purpose")
                    ss_res = gr.Textbox(label="result")
                    ss_k = gr.Number(label="TOPK", value=10)
                    ss_btn = gr.Button("▶ 子空间搜索")
                    gr.Markdown("**全空间搜索**")
                    fs_time = gr.Textbox(label="time")
                    fs_subj = gr.Textbox(label="subject")
                    fs_act = gr.Textbox(label="action")
                    fs_obj = gr.Textbox(label="object")
                    fs_purp = gr.Textbox(label="purpose")
                    fs_res = gr.Textbox(label="result")
                    fs_k = gr.Number(label="TOPK", value=10)
                    fs_btn = gr.Button("▶ 全空间搜索")
                with gr.Column(scale=2):
                    search_out = gr.DataFrame(headers=SEARCH_HEADERS, label="搜索结果", max_height=500)

        # ---------- Tab 3: 自然语言查询 ----------
        with gr.Tab("🧠 自然语言查询"):
            gr.Markdown("""
            **使用自然语言查询记忆！**
            示例："查询我关于 Python 的记忆"、"我最近的学习记录"
            """)
            with gr.Row():
                nl_query_in = gr.Textbox(label="自然语言查询", placeholder="输入你的问题...", scale=4)
                nl_topk = gr.Number(label="返回条数", value=5, scale=1)
            nl_query_btn = gr.Button("🔮 查询", variant="primary")
            nl_out = gr.Textbox(label="查询结果", lines=15, interactive=False)

        # ---------- Tab 4: MQL 查询 ----------
        with gr.Tab("📝 MQL 查询"):
            gr.Markdown("""
            **MQL 示例:**
            ```
            SELECT * FROM memories WHERE subject='我' LIMIT 10
            INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)
            SELECT * FROM memories WHERE [subject='我'] SEARCH TOPK 5
            ```
            """)
            mql_in = gr.Textbox(label="MQL 语句", placeholder="SELECT * FROM memories ...", lines=6)
            mql_exec_btn = gr.Button("▶ 执行", variant="primary")
            with gr.Column():
                mql_status = gr.Textbox(label="状态", interactive=False, lines=1)
                mql_out = gr.DataFrame(headers=MQL_HEADERS, label="查询结果", max_height=500)

        # ---------- Tab 5: 系统管理 (管理员) ----------
        if pre_auth:
            with gr.Tab("⚙️ 系统管理"):
                gr.Markdown("**创建新系统**")
                with gr.Row():
                    ns_name = gr.Textbox(label="系统名称", placeholder="my_system")
                    cs_btn = gr.Button("➕ 创建系统")
                cs_status = gr.Textbox(label="状态", interactive=False)

                gr.Markdown("---")
                gr.Markdown("**持有者名称（NL 查询时用于将'我'映射到正确的人称）**")
                holder_in = gr.Textbox(
                    label="持有者",
                    placeholder="星织",
                    value=do_get_holder(),
                )
                holder_save_btn = gr.Button("💾 保存持有者")
                holder_status = gr.Textbox(label="状态", interactive=False)

                gr.Markdown("---")
                gr.Markdown("**LLM 统计**")
                stats_display = gr.Textbox(label="统计", value=do_get_llm_stats_text(), interactive=False)
                stats_refresh_btn = gr.Button("🔄 刷新统计")
                stats_reset_btn = gr.Button("🔢 重置计数器")
                stats_reset_status = gr.Textbox(label="状态", interactive=False)

        # =========================================================================
        # 事件绑定
        # =========================================================================

        switch_btn.click(
            do_switch_system,
            inputs=[system_sel],
            outputs=[table_out, status_out, system_sel],
        )

        refresh_btn.click(
            do_refresh,
            outputs=[table_out, status_out],
        )

        add_btn.click(
            do_add,
            inputs=[time_in, subject_in, action_in, object_in, purpose_in, result_in, content_in, lifecycle_in],
            outputs=[table_out, status_out],
        )

        del_btn.click(
            do_delete,
            inputs=[mid_del],
            outputs=[table_out, status_out],
        )

        ss_btn.click(
            do_subspace_search,
            inputs=[ss_time, ss_subj, ss_act, ss_obj, ss_purp, ss_res, ss_k],
            outputs=[search_out],
        )

        fs_btn.click(
            do_fullspace_search,
            inputs=[fs_time, fs_subj, fs_act, fs_obj, fs_purp, fs_res, fs_k],
            outputs=[search_out],
        )

        nl_query_btn.click(
            do_nl_query,
            inputs=[nl_query_in, nl_topk],
            outputs=[nl_out],
        )

        mql_exec_btn.click(
            do_mql,
            inputs=[mql_in],
            outputs=[mql_out, mql_status],
        )

        if pre_auth:
            cs_btn.click(
                do_create_system,
                inputs=[ns_name],
                outputs=[cs_status],
            )

            holder_save_btn.click(
                do_set_holder,
                inputs=[holder_in],
                outputs=[holder_status],
            )

            stats_refresh_btn.click(
                do_get_llm_stats_text,
                outputs=[stats_display],
            )

            stats_reset_btn.click(
                do_reset_llm_stats,
                outputs=[stats_reset_status],
            )

    return app


def launch_gradio(gradio_port: int = 7860, pre_auth: bool = False, admin_user: str = "admin"):
    app = build_app(pre_auth=pre_auth, admin_user=admin_user)
    print(f"  Gradio WebUI: http://127.0.0.1:{gradio_port}/")
    app.launch(
        server_name="0.0.0.0",
        server_port=gradio_port,
        share=False,
    )


if __name__ == "__main__":
    launch_gradio(gradio_port=7860, pre_auth=False)
