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

SLOT_NAMES = ["scene", "subject", "action", "object", "purpose", "result"]
TABLE_HEADERS = ["ID", "scene", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "创建时间", "更新时间"]
SEARCH_HEADERS = ["ID", "scene", "subject", "action", "object", "purpose", "result", "lifecycle", "距离", "匹配槽位"]
MQL_HEADERS = ["ID", "scene", "subject", "action", "object", "purpose", "result", "内容(前80字)", "lifecycle", "创建时间"]

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
                "scene": parts.get("scene", ""),
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
                "scene": parts.get("scene", ""),
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
                "scene": parts.get("scene", ""),
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


def do_add(scene_v, subject_v, action_v, object_v, purpose_v, result_v, content, lifecycle):
    if not content.strip():
        return pd.DataFrame(), "❌ 内容不能为空"
    qs = f"<{scene_v}><{subject_v}><{action_v}><{object_v}><{purpose_v}><{result_v}>"
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

def do_subspace_search(scene_v, subject_v, action_v, object_v, purpose_v, result_v, top_k):
    qs = {k: v for k, v in {
        "scene": scene_v, "subject": subject_v, "action": action_v,
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


def do_fullspace_search(scene_v, subject_v, action_v, object_v, purpose_v, result_v, top_k):
    qs = {k: v for k, v in {
        "scene": scene_v, "subject": subject_v, "action": action_v,
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

def _render_flowchart(trace: list[dict]) -> str:
    if not trace:
        return "（无跟踪数据）"
    lines = []
    lines.append("┌──────────────────────────────────────────────────────────────┐")
    lines.append("│                      🔬 Pipeline Flow                      │")
    lines.append("├──────────────────────────────────────────────────────────────┤")

    groups = []
    current_step = None
    for t in trace:
        s = t.get("step", "")
        if s != current_step:
            if groups and groups[-1]["calls"]:
                pass
            groups.append({"step": s, "calls": []})
            current_step = s
        groups[-1]["calls"].append(t)

    total_tok = 0
    total_ms = 0
    step_index = 0
    for g in groups:
        if not g["step"]:
            continue
        step_index += 1
        calls = g["calls"]
        n_calls = len(calls)
        p_tok = sum(c.get("prompt_tokens", 0) for c in calls)
        c_tok = sum(c.get("completion_tokens", 0) for c in calls)
        t_tok = p_tok + c_tok
        total_tok += t_tok
        d_ms = sum(c.get("duration_ms", 0) for c in calls)
        total_ms += d_ms

        lines.append(f"│                                                              │")
        label = g["step"]
        tok_str = f"prompt {p_tok} + 完成 {c_tok} = {t_tok} tok" if t_tok else "0 tok"
        time_str = f"  {d_ms:.0f}ms" if d_ms >= 1 else ""
        lines.append(f"│  {label}")
        if n_calls > 0:
            lines.append(f"│    LLM ×{n_calls}  ·  {tok_str}{time_str}")

        # Show output previews
        for ci, c in enumerate(calls):
            out = c.get("output_preview", "").strip()
            if out:
                trunc = out[:100]
                label2 = f"  └─" if n_calls == 1 else f"  ├─调用{ci+1}:"
                lines.append(f"│{label2} {trunc}")

        # Track non-LLM step notes from steps_summary
        if "写入" in label or "执行" in label or "重排" in label or "混合" in label:
            pass  # steps_summary covers these

    lines.append("│                                                              │")
    lines.append("├──────────────────────────────────────────────────────────────┤")

    n_llm = len(trace)
    avg_ms = total_ms / n_llm if n_llm else 0
    lines.append(f"│  📊 总计: {n_llm} 次 LLM 调用  ·  {total_tok} tokens  ·  {total_ms:.0f}ms  ·  平均 {avg_ms:.0f}ms/调用")
    lines.append("└──────────────────────────────────────────────────────────────┘")
    return "\n".join(lines)


def _lifecycle_human(seconds: int) -> str:
    if seconds >= 999999:
        return "永久"
    if seconds >= 86400 * 30:
        return f"{seconds // 86400}天"
    if seconds >= 86400:
        return f"{seconds // 86400}天"
    if seconds >= 3600:
        return f"{seconds // 3600}小时"
    return f"{seconds}秒"


def _item_slots_str(item: dict) -> str:
    parts = _parse_sentence_parts(item.get("query_sentence", ""))
    active = {k: v for k, v in parts.items() if v and v != "<无>"}
    if not active:
        return ""
    return " | ".join(f"{k}={v}" for k, v in active.items())


def _format_item_detail(item: dict, index: int) -> list[str]:
    lines = []
    content = item.get("content", "") or item.get("query_sentence", "")
    mid = item.get("id", item.get("memory_id", ""))[:12]
    lifecycle = item.get("lifecycle", 0)
    created = str(item.get("created_at", ""))[:16]
    distance = item.get("distance", None)
    score = item.get("score", None)

    lines.append(f"  #{index}  {content[:120]}")
    slots_str = _item_slots_str(item)
    if slots_str:
        lines.append(f"      槽位: {slots_str}")
    parts = [f"id={mid}", f"周期={_lifecycle_human(lifecycle)}"]
    if created:
        parts.append(f"创建={created}")
    if distance is not None:
        parts.append(f"距离={distance:.4f}")
    if score is not None:
        parts.append(f"分数={score:.4f}")
    lines.append(f"      [{', '.join(parts)}]")
    return lines


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
        lines.append("─" * 72)

        # ── 流程图 ──
        trace = result.get("trace", [])
        if trace:
            lines.append(_render_flowchart(trace))

        # ── 意图识别概览 ──
        intent = result.get("intent", {})
        n_writes = len(intent.get("writes", []))
        n_queries = len(intent.get("queries", []))
        pipeline_type = result.get("type", "query_only")

        type_icons = {"write_only": "📝 写入", "query_only": "🔍 查询", "mixed": "📝🔍 写入+查询", "empty": "❓ 空"}
        lines.append(f"📋 类型: {type_icons.get(pipeline_type, pipeline_type)}")
        if n_writes > 0 or n_queries > 0:
            parts = []
            if n_writes > 0:
                parts.append(f"📝 {n_writes}条写入")
            if n_queries > 0:
                parts.append(f"🔍 {n_queries}条查询")
            lifecycle = intent.get("lifecycle", "short")
            lines.append(f"   意图: {', '.join(parts)}  |  档位: {lifecycle} ({_lifecycle_human(intent.get('reference_duration', 86400))})")

        llm_calls = result.get("llm_calls", 0)
        lines.append(f"🔄 本次 LLM 调用: {llm_calls} 次")
        lines.append("")

        # ── 意图详情 ──
        if intent.get("writes"):
            lines.append("📝 写入句:")
            for w in intent["writes"]:
                lines.append(f"   → {w}")
            lines.append("")
        if intent.get("queries"):
            lines.append("🔍 查询句:")
            for q in intent["queries"]:
                lines.append(f"   → {q}")
            lines.append("")

        # ── 写入执行结果 ──
        if pipeline_type in ("write_only", "mixed"):
            writes = result.get("writes", [])
            if writes:
                lines.append(f"✅ 写入结果 ({len(writes)} 条):")
                for w in writes:
                    wtype = getattr(w, "type", "?")
                    wmsg = getattr(w, "message", "")
                    wids = getattr(w, "memory_ids", [])
                    lines.append(f"   [{wtype}] {wmsg}")
                    if wids:
                        lines.append(f"   记忆ID: {', '.join(wids[:3])}")
                lines.append("")
            else:
                if n_writes > 0:
                    lines.append("⚠️ 写入执行结果为空（可能 LLM 生成 MQL 失败）")
                    lines.append("")

        # ── 每个查询的结果 ──
        query_results_list = result.get("queries", [])
        if query_results_list:
            for qi, qr in enumerate(query_results_list):
                q_query = qr.get("query", f"查询{qi+1}")
                q_response = qr.get("response", "")
                q_mql = qr.get("mql", "")
                q_items = qr.get("results", [])

                label = f"查询 {qi+1}" if len(query_results_list) > 1 else "查询"
                lines.append(f"══ {label}: {q_query} ══")

                if q_mql:
                    lines.append(f"   MQL: {q_mql}")

                if q_response:
                    lines.append(f"   💬 {q_response}")

                if q_items:
                    lines.append(f"   📄 {len(q_items)} 条相关记忆:")
                    for i, item in enumerate(q_items, 1):
                        lines.extend(_format_item_detail(item, i))
                else:
                    lines.append("   📄 无相关记忆")

                lines.append("")

        # ── 兼容旧接口: 平面列表 ──
        items = result.get("result", [])
        if items and not query_results_list:
            lines.append("📄 检索结果:")
            for i, item in enumerate(items, 1):
                lines.extend(_format_item_detail(item, i))
            lines.append("")

        # ── 生成的所有 MQL ──
        if result.get("mql"):
            lines.append("─" * 72)
            lines.append(f"📝 完整 MQL:")
            lines.append(f"   {result['mql']}")
            lines.append("")

        # ── 全局统计 ──
        gs = result.get("global_stats", {})
        if gs:
            lines.append(f"📊 全局统计: 累计 {gs.get('total_calls', 0)} 次 LLM 调用 / {gs.get('total_queries', 0)} 次查询")

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
                    scene_in = gr.Textbox(label="scene", placeholder="平时")
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
                    ss_scene = gr.Textbox(label="scene")
                    ss_subj = gr.Textbox(label="subject")
                    ss_act = gr.Textbox(label="action")
                    ss_obj = gr.Textbox(label="object")
                    ss_purp = gr.Textbox(label="purpose")
                    ss_res = gr.Textbox(label="result")
                    ss_k = gr.Number(label="TOPK", value=10)
                    ss_btn = gr.Button("▶ 子空间搜索")
                    gr.Markdown("**全空间搜索**")
                    fs_scene = gr.Textbox(label="scene")
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
            ── 查询 ──
            SELECT * FROM memories WHERE subject='星织' LIMIT 10
            SELECT * FROM memories WHERE [subject='星织'] SEARCH TOPK 5
            SELECT * FROM memories WHERE subject='星织' TIME year(2026) AND month(04)

            ── 写入（六槽：scene/subject/action/object/purpose/result）──
            INSERT INTO memories VALUES ('<所有><星织><的><名字><名字><星织>', '星织的名字是星织', 2592000)
            INSERT INTO memories VALUES ('<所有><星织><有><哥哥><关系><绯绯>', '星织有个哥哥叫绯绯', 2592000)

            ── 删除 ──
            DELETE FROM memories WHERE subject='星织'
            DELETE FROM memories
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
            inputs=[scene_in, subject_in, action_in, object_in, purpose_in, result_in, content_in, lifecycle_in],
            outputs=[table_out, status_out],
        )

        del_btn.click(
            do_delete,
            inputs=[mid_del],
            outputs=[table_out, status_out],
        )

        ss_btn.click(
            do_subspace_search,
            inputs=[ss_scene, ss_subj, ss_act, ss_obj, ss_purp, ss_res, ss_k],
            outputs=[search_out],
        )

        fs_btn.click(
            do_fullspace_search,
            inputs=[fs_scene, fs_subj, fs_act, fs_obj, fs_purp, fs_res, fs_k],
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
