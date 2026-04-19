"""
星尘记忆系统 - Gradio WebUI
APIKey登录 + 多记忆系统管理 + MQL查询 + 记忆CRUD + 向量搜索 + 用户管理（管理员）
"""

import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import gradio as gr
import pandas as pd

# ============================================================================
# 全局状态
# ============================================================================

_api_server: Optional["APIServer"] = None  # 由 start_server.py 注入
_auth_context = None

# 槽位名称（与 embedding_coder.SLOT_NAMES 保持一致）
SLOT_NAMES = ["time", "subject", "action", "object", "purpose", "result"]
_is_admin = False
_active_system_name = None
_pre_authenticated = False  # start_server.py 注入时为 True

TABLE_HEADERS = ["ID", "time", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "创建时间", "更新时间"]
SEARCH_HEADERS = ["ID", "time", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "距离", "匹配槽位"]


# ============================================================================
# 初始化（由 start_server.py 调用）
# ============================================================================

def init_webui(api_server: "APIServer", auth_username: str, is_admin: bool):
    """
    由 start_server.py 调用，注入已创建的 APIServer 实例。
    此时 admin 已通过 config.toml 的 api_key 完成认证。
    """
    global _api_server, _auth_context, _is_admin, _active_system_name, _pre_authenticated
    _api_server = api_server
    _is_admin = is_admin
    _pre_authenticated = True

    # 构建 auth_context：使用真正的 AuthContext，而非伪造对象
    from xcmemory_interest.user_manager import AuthContext, PermissionType
    _auth_context = AuthContext(
        username=auth_username,
        is_superadmin=is_admin,
        permissions={"*": [PermissionType.ADMIN]} if is_admin else {},
    )

    # 设置激活系统（使用 active_system_name 避免访问 .name 时类型错误）
    current_name = _api_server.pyapi.active_system_name
    if current_name:
        _active_system_name = current_name
    else:
        systems = _api_server.pyapi.list_all_systems()
        if systems:
            first = systems[0]["name"]
            _api_server.pyapi.set_active_system(first)
            _active_system_name = first


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
                "内容(前80字)": content_val + ("..." if len(str(row.get("content", "") or "")) > 80 else ""),
                "lifecycle": str(row.get("lifecycle", "")),
                "距离": row.get("distance", ""),
                "匹配槽位": row.get("match_count", ""),
            })
    return pd.DataFrame(rows, columns=SEARCH_HEADERS)


def _exec_mql(mql: str):
    global _api_server, _auth_context
    if _api_server is None:
        return None, "❌ 未连接服务器"
    try:
        from xcmemory_interest.mql.interpreter_extended import InterpreterExtended
        interpreter = InterpreterExtended()
        interpreter.bind("api", _api_server.pyapi)
        interpreter.set_auth_context(_auth_context)
        # 绑定 UserManager（用户管理语句需要）
        interpreter.bind("um", _api_server.user_manager)
        # 始终绑定当前活跃系统（即使为 None，也避免 _get_memory_system fallback 到 api）
        interpreter.bind("mem", _api_server.pyapi.active_system)
        result = interpreter.execute(mql)
        return result, None
    except Exception as e:
        return None, f"❌ 执行失败: {str(e)}"


# ============================================================================
# 登录（外部注入时跳过）
# ============================================================================

def do_login(data_root, api_key):
    global _api_server, _auth_context, _is_admin, _active_system_name, _pre_authenticated

    if _pre_authenticated:
        # 已由 start_server.py 注入，直接返回已登录状态
        systems = _api_server.pyapi.list_all_systems()
        system_names = [s["name"] for s in systems]
        # 恢复上次活跃的系统（从 meta 读取 _active_system 并加载到 _systems）
        last_active = _api_server.pyapi._active_system
        if last_active and last_active in [s["name"] for s in systems]:
            _api_server.pyapi.set_active_system(last_active)
        elif system_names:
            _api_server.pyapi.set_active_system(system_names[0])
        user_label = "👑 admin (管理员)" if _is_admin else "👤 admin"
        return (
            f"✅ 欢迎 {user_label}（已通过配置注入）",
            gr.update(choices=system_names, value=system_names[0] if system_names else None),
            gr.update(visible=True),
            gr.update(visible=False),
        )

    if not data_root or not os.path.isdir(data_root):
        return "❌ 无效目录", None, gr.update(), gr.update()

    if not api_key:
        return "❌ 请输入 APIKey", None, gr.update(), gr.update()

    try:
        from xcmemory_interest.user_manager import AuthContext
        _api_server = APIServer(database_root=data_root, debug=False)
        auth_result = _api_server.user_manager.authenticate(api_key)

        if not auth_result.success:
            return f"❌ 认证失败: {auth_result.error}", None, gr.update(), gr.update()

        _auth_context = AuthContext.from_auth_result(auth_result)
        username = auth_result.username
        _is_admin = auth_result.user.is_superadmin if auth_result.user else False

        systems = _api_server.pyapi.list_all_systems()
        system_names = [s["name"] for s in systems]

        if system_names and _api_server.pyapi.active_system is None:
            _api_server.pyapi.set_active_system(system_names[0])
            _active_system_name = system_names[0]
        elif _api_server.pyapi.active_system_name:
            _active_system_name = _api_server.pyapi.active_system_name

        user_label = f"👑 {username} (管理员)" if _is_admin else f"👤 {username}"

        return (
            f"✅ 欢迎 {user_label}",
            gr.update(choices=system_names, value=system_names[0] if system_names else None),
            gr.update(visible=True),
            gr.update(visible=False),
        )
    except Exception as e:
        return f"❌ 初始化失败: {str(e)}", None, gr.update(), gr.update()


# ============================================================================
# 系统切换
# ============================================================================

def do_switch_system(system_name):
    global _api_server, _active_system_name
    if _api_server is None or not system_name:
        return "❌ 未选择系统"
    result, err = _exec_mql(f"USE {system_name}")
    if err:
        return err
    _active_system_name = system_name
    return f"✅ 已切换到: {system_name}"


# ============================================================================
# 记忆表
# ============================================================================

def _refresh_system_list():
    """刷新系统列表并返回 gr.update"""
    systems = _api_server.pyapi.list_all_systems()
    choices = [s["name"] for s in systems]
    current = _api_server.pyapi.active_system_name or (choices[0] if choices else None)
    return gr.update(choices=choices, value=current)


def do_refresh():
    # 无活跃系统时直接返回空表，避免 interpreter fallback 到 api 对象
    if _api_server is None or _api_server.pyapi.active_system is None:
        return pd.DataFrame(columns=TABLE_HEADERS), "ℹ️ 请先选择一个记忆系统", gr.update()
    result, err = _exec_mql("SELECT * FROM memories ORDER BY created_at DESC LIMIT 200")
    if err:
        return pd.DataFrame(columns=TABLE_HEADERS), err, gr.update()
    # 同时刷新系统列表
    sys_update = _refresh_system_list()
    return _build_df(result.data if result.data else [], TABLE_HEADERS), "✅ 已刷新", sys_update


def do_search(keyword, slot_filter, lifecycle_filter, top_k):
    """通过 MQL 搜索记忆（全部走 MQL，不直接调底层接口）"""
    # 无活跃系统时直接返回空表
    if _api_server is None or _api_server.pyapi.active_system is None:
        return pd.DataFrame(columns=TABLE_HEADERS)
    conditions = []
    if keyword:
        conditions.append(f"content LIKE '%{keyword}%'")
    if slot_filter and slot_filter != "全部":
        # MQL WHERE 支持槽位名 = '值' 的过滤
        conditions.append(f"{slot_filter} != ''")
    if lifecycle_filter and lifecycle_filter != "全部":
        conditions.append(f"lifecycle = {int(lifecycle_filter)}")

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = min(int(top_k), 200)
    result, err = _exec_mql(f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT {limit}")
    if err:
        return pd.DataFrame(columns=TABLE_HEADERS)
    return _build_df(result.data if result.data else [], TABLE_HEADERS)


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


def do_update(mid, time_v, subject_v, action_v, object_v, purpose_v, result_v, content, lifecycle):
    if not mid:
        return pd.DataFrame(), "❌ 请填写记忆ID"
    if not content.strip():
        return pd.DataFrame(), "❌ 内容不能为空"
    sets = [f"content = '{content.strip()}'", f"lifecycle = {int(lifecycle)}"]
    if time_v or subject_v or action_v or object_v or purpose_v or result_v:
        qs = f"<{time_v}><{subject_v}><{action_v}><{object_v}><{purpose_v}><{result_v}>"
        sets.append(f"query_sentence = '{qs}'")
    result, err = _exec_mql(f"UPDATE memories SET {', '.join(sets)} WHERE id = '{mid}'")
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
        return "❌ 请输入 MQL 语句"
    try:
        from xcmemory_interest.mql.interpreter_extended import InterpreterExtended
        interpreter = InterpreterExtended()
        interpreter.bind("api", _api_server.pyapi)
        interpreter.set_auth_context(_auth_context)
        interpreter.bind("um", _api_server.user_manager)
        active = _api_server.pyapi.active_system
        if active:
            interpreter.bind("mem", active)

        if ";" in mql_script.strip():
            results = interpreter.execute_script(mql_script.strip())
            lines = [f"✅ 执行成功，影响 {sum(r.affected_rows or 0 for r in results)} 行"]
            for i, r in enumerate(results):
                if r.data:
                    lines.append(f"\n--- 语句 {i+1} ---")
                    lines.append(f"影响行数: {r.affected_rows}")
                    for row in r.data:
                        lines.append(str(row))
                elif r.message:
                    lines.append(f"\n--- 语句 {i+1} ---")
                    lines.append(r.message)
            return "\n".join(lines)
        else:
            result = interpreter.execute(mql_script.strip())
            if result.data:
                lines = [f"✅ 查询成功 ({len(result.data)} 行)"]
                for row in result.data:
                    lines.append(str(row))
                if result.affected_rows is not None:
                    lines.insert(0, f"✅ 影响 {result.affected_rows} 行")
                return "\n".join(lines)
            elif result.message:
                return f"✅ {result.message}"
            return "✅ 执行成功"
    except Exception as e:
        return f"❌ 执行失败: {str(e)}"


# ============================================================================
# 系统管理（管理员）
# ============================================================================

def do_create_system(name, enable_interest):
    if not name.strip():
        return "❌ 系统名称不能为空", None
    result, err = _exec_mql(f"CREATE DATABASE {name.strip()}")
    if err:
        return err, None
    # 刷新系统列表
    r2, _ = _exec_mql("LIST DATABASES")
    choices = [s["name"] for s in (r2.data if r2 and r2.data else [])]
    return f"✅ 系统 '{name}' 已创建", gr.update(choices=choices, value=name.strip())


def do_delete_system(name):
    if not name:
        return "❌ 请选择要删除的系统", None
    result, err = _exec_mql(f"DROP DATABASE {name}")
    if err:
        return err, None
    r2, _ = _exec_mql("LIST DATABASES")
    choices = [s["name"] for s in (r2.data if r2 and r2.data else [])]
    first = choices[0] if choices else None
    return f"✅ 系统 '{name}' 已删除", gr.update(choices=choices, value=first)


# ============================================================================
# 用户管理（管理员）
# ============================================================================

def do_load_users():
    result, err = _exec_mql("LIST USERS")
    if err or not result or not result.data:
        return pd.DataFrame()
    rows = []
    for u in result.data:
        perms = ", ".join([f"{p['system']}:{p['permission']}" for p in u.get("permissions", [])])
        rows.append({
            "用户名": u.get("username", ""),
            "类型": "管理员" if u.get("is_superadmin") else "普通用户",
            "APIKey": "✓ 已设置" if u.get("has_api_key") else "✗ 未设置",
            "创建时间": u.get("created_at", "")[:16],
            "权限": perms or "-",
        })
    return pd.DataFrame(rows, columns=["用户名", "类型", "APIKey", "创建时间", "权限"])


def do_create_user(username):
    if not username.strip():
        return "❌ 用户名不能为空", None
    result, err = _exec_mql(f"CREATE USER {username.strip()}")
    if err:
        return err, None
    # MQL CREATE USER 返回 api_key 在 data 中
    api_key = ""
    if result and result.data and len(result.data) > 0:
        api_key = result.data[0].get("api_key", "")
    msg = f"✅ 用户已创建！APIKey: {api_key}" if api_key else f"✅ 用户已创建"
    return msg, gr.update()


def do_delete_user(username):
    if not username:
        return "❌ 请选择用户", None
    result, err = _exec_mql(f"DROP USER {username}")
    if err:
        return err, None
    return f"✅ 用户 '{username}' 已删除", gr.update()


def do_grant_perm(username, system_name, perm_type):
    if not username or not system_name:
        return "❌ 用户名和系统名称不能为空"
    result, err = _exec_mql(f"GRANT {perm_type} ON {system_name} TO {username}")
    if err:
        return err
    return f"✅ 已授予 {username} 在 {system_name} 上的 {perm_type} 权限"


# ============================================================================
# 构建 Gradio UI
# ============================================================================

_CSS = """
.success-box {background: #d4edda; border: 1px solid #c3e6cb; border-radius: 8px; padding: 12px; margin: 8px 0;}
.error-box {background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 12px; margin: 8px 0;}
"""






def build_app(pre_auth: bool = False, admin_user: str = "admin") -> gr.Blocks:
    """构建 Gradio Blocks 应用。pre_auth=True 时为注入模式（免登录）。"""

    with gr.Blocks(title="星尘记忆系统") as app:
        gr.Markdown("## 🌟 星尘记忆系统 - 可视化管理")

        # ---- 登录栏 ----
        with gr.Row(visible=not pre_auth) as login_row:
            data_root_in = gr.Textbox(label="数据库根目录", placeholder="O:/project/xcmemory_interest/data", scale=3)
            api_key_in = gr.Textbox(label="APIKey", placeholder="xi-admin-xxxx 或 xi-user-xxxx", type="password", scale=3)
            login_btn = gr.Button("连接", variant="primary", scale=1)

        status_out = gr.Textbox(label="状态", show_label=False, lines=1, interactive=False)

        if pre_auth:
            gr.Markdown("**✅ 已通过 `start_server.py --gradio` 注入管理权限，无需登录**")

        # ---- 主界面 ----
        with gr.Column(visible=False) as main_col:
            with gr.Row():
                system_sel = gr.Dropdown(label="记忆系统", choices=[], scale=4)
                refresh_btn = gr.Button("🔄 刷新")

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
                        with gr.Row():
                            kw_in = gr.Textbox(label="搜索关键词", placeholder="输入内容关键词...", scale=2)
                            slot_filt = gr.Dropdown(["全部"] + SLOT_NAMES, value="全部", label="槽位", scale=1)
                            lc_filt = gr.Dropdown(["全部", "86400", "604800", "2592000", "31536000"], value="全部", label="生命周期", scale=1)
                            topk_in = gr.Number(label="条数", value=50, scale=1)
                            search_btn = gr.Button("🔍 搜索", scale=1)
                        table_out = gr.DataFrame(headers=TABLE_HEADERS, label="记忆列表", max_height=400)
                        with gr.Row():
                            mid_upd = gr.Textbox(label="记忆ID (更新/删除)", scale=2)
                            upd_content = gr.Textbox(label="新内容", scale=3)
                            upd_lc = gr.Number(label="新lifecycle", value=86400, scale=1)
                            upd_btn = gr.Button("✏️ 更新", variant="secondary")
                            del_btn = gr.Button("🗑 删除", variant="stop")

            # ---------- Tab 2: 向量搜索 ----------
            with gr.Tab("🔍 向量搜索"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("**子空间搜索**")
                        ss_time = gr.Textbox(label="time", placeholder="平时")
                        ss_subj = gr.Textbox(label="subject", placeholder="我")
                        ss_act = gr.Textbox(label="action", placeholder="学习")
                        ss_obj = gr.Textbox(label="object", placeholder="编程")
                        ss_purp = gr.Textbox(label="purpose", placeholder="成长")
                        ss_res = gr.Textbox(label="result", placeholder="有收获")
                        ss_k = gr.Number(label="TOPK", value=10)
                        ss_btn = gr.Button("▶ 子空间搜索")
                        gr.Markdown("**全空间搜索**")
                        fs_time = gr.Textbox(label="time", placeholder="平时")
                        fs_subj = gr.Textbox(label="subject", placeholder="我")
                        fs_act = gr.Textbox(label="action", placeholder="学习")
                        fs_obj = gr.Textbox(label="object", placeholder="编程")
                        fs_purp = gr.Textbox(label="purpose", placeholder="成长")
                        fs_res = gr.Textbox(label="result", placeholder="有收获")
                        fs_k = gr.Number(label="TOPK", value=10)
                        fs_btn = gr.Button("▶ 全空间搜索")
                    with gr.Column(scale=2):
                        search_out = gr.DataFrame(headers=SEARCH_HEADERS, label="搜索结果", max_height=500)

            # ---------- Tab 3: MQL 查询 ----------
            with gr.Tab("📝 MQL 查询"):
                gr.Markdown("""
                **MQL 示例:**
                ```
                SELECT * FROM memories WHERE subject='我' LIMIT 10
                INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)
                SELECT * FROM memories WHERE [subject='我', action='学习'] SEARCH TOPK 5
                ```
                """)
                mql_in = gr.Textbox(label="MQL 语句", placeholder="SELECT * FROM memories ...", lines=6)
                with gr.Row():
                    mql_exec_btn = gr.Button("▶ 执行 (; 分隔多行)", variant="primary")
                    mql_clear_btn = gr.Button("🗑 清空")
                mql_out = gr.Textbox(label="执行结果", lines=12, interactive=False)

            # ---------- Tab 4: 系统管理 (管理员) ----------
            with gr.Tab("⚙️ 系统管理"):
                gr.Markdown("**创建新系统**")
                with gr.Row():
                    ns_name = gr.Textbox(label="系统名称", placeholder="my_system")
                    ns_interest = gr.Checkbox(label="启用兴趣模式", value=False)
                    cs_btn = gr.Button("➕ 创建系统")
                cs_status = gr.Textbox(label="状态", interactive=False)
                gr.Markdown("**删除系统**")
                with gr.Row():
                    ds_sel = gr.Dropdown(label="选择系统", choices=[])
                    ds_btn = gr.Button("🗑 删除系统", variant="stop")
                ds_status = gr.Textbox(label="状态", interactive=False)

            # ---------- Tab 5: 用户管理 (管理员) ----------
            with gr.Tab("👥 用户管理"):
                with gr.Row():
                    lu_btn = gr.Button("🔄 刷新用户")
                    cu_btn = gr.Button("➕ 新建用户")
                users_tbl = gr.DataFrame(headers=["用户名", "类型", "APIKey", "创建时间", "权限"], label="用户列表")
                gr.Markdown("**新建用户**")
                with gr.Row():
                    nu_in = gr.Textbox(label="用户名", placeholder="newuser")
                    cu2_btn = gr.Button("创建")
                cu_status = gr.Textbox(label="状态", interactive=False)
                gr.Markdown("**删除用户**")
                with gr.Row():
                    du_sel = gr.Dropdown(label="选择用户", choices=[])
                    du_btn = gr.Button("🗑 删除用户", variant="stop")
                du_status = gr.Textbox(label="状态", interactive=False)
                gr.Markdown("**授予权限**")
                with gr.Row():
                    pu_in = gr.Textbox(label="用户名")
                    ps_in = gr.Textbox(label="系统名称 (*=所有)")
                    pt_sel = gr.Dropdown(["read", "write"], value="read")
                    gp_btn = gr.Button("✅ 授予权限")
                gp_status = gr.Textbox(label="状态", interactive=False)

        # =========================================================================
        # 事件绑定
        # =========================================================================
        if pre_auth:
            # 注入模式下，直接显示主界面并预加载
            main_col.visible = True
            login_row.visible = False
        else:
            login_btn.click(
                do_login,
                inputs=[data_root_in, api_key_in],
                outputs=[status_out, system_sel, main_col, login_row]
            )

        system_sel.change(do_switch_system, inputs=[system_sel], outputs=[status_out])

        # 记忆管理
        refresh_btn.click(do_refresh, outputs=[table_out, status_out, system_sel])
        search_btn.click(do_search, inputs=[kw_in, slot_filt, lc_filt, topk_in], outputs=[table_out])
        add_btn.click(do_add, inputs=[time_in, subject_in, action_in, object_in, purpose_in, result_in, content_in, lifecycle_in], outputs=[table_out, status_out])
        upd_btn.click(do_update, inputs=[mid_upd, time_in, subject_in, action_in, object_in, purpose_in, result_in, upd_content, upd_lc], outputs=[table_out, status_out])
        del_btn.click(do_delete, inputs=[mid_upd], outputs=[table_out, status_out])

        # 向量搜索
        ss_btn.click(do_subspace_search, inputs=[ss_time, ss_subj, ss_act, ss_obj, ss_purp, ss_res, ss_k], outputs=[search_out])
        fs_btn.click(do_fullspace_search, inputs=[fs_time, fs_subj, fs_act, fs_obj, fs_purp, fs_res, fs_k], outputs=[search_out])

        # MQL
        mql_exec_btn.click(do_mql, inputs=[mql_in], outputs=[mql_out])
        mql_clear_btn.click(lambda: ("", ""), inputs=None, outputs=[mql_in, mql_out])

        # 系统管理
        cs_btn.click(do_create_system, inputs=[ns_name, ns_interest], outputs=[cs_status, system_sel])
        ds_btn.click(do_delete_system, inputs=[ds_sel], outputs=[ds_status, system_sel])

        # 用户管理
        lu_btn.click(do_load_users, outputs=[users_tbl])
        cu2_btn.click(do_create_user, inputs=[nu_in], outputs=[cu_status, status_out])
        du_btn.click(do_delete_user, inputs=[du_sel], outputs=[du_status, status_out])
        gp_btn.click(do_grant_perm, inputs=[pu_in, ps_in, pt_sel], outputs=[gp_status])

        # 全局 load 事件：应用启动时初始化系统列表
        def _load_system_list():
            if _api_server is None:
                return gr.update(choices=[])
            systems = _api_server.pyapi.list_all_systems()
            choices = [s["name"] for s in systems]
            current = _api_server.pyapi.active_system_name or (choices[0] if choices else None)
            return gr.update(choices=choices, value=current)
        app.load(_load_system_list, outputs=[system_sel])

    return app


def launch_gradio(gradio_port: int = 7860, pre_auth: bool = False, admin_user: str = "admin"):
    """启动 Gradio WebUI（阻塞）。pre_auth=True 时免登录。"""
    app = build_app(pre_auth=pre_auth, admin_user=admin_user)
    print(f"  Gradio WebUI: http://127.0.0.1:{gradio_port}/")
    app.launch(
        server_name="0.0.0.0",
        server_port=gradio_port,
        share=False,
        css=_CSS,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    # 独立运行模式（需手动输入 APIKey 登录）
    launch_gradio(gradio_port=7860, pre_auth=False)