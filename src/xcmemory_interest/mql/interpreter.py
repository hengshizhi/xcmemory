"""
MQL - Memory Query Language 解释器

执行 DSL 语句，操作记忆系统
"""

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING
from dataclasses import dataclass

from .parser import (
    ASTNode, SelectStatement, InsertStatement,
    UpdateStatement, DeleteStatement, Condition,
    DefineStatement, GraphClause, TimeFilter
)
from .errors import ExecutionError

if TYPE_CHECKING:
    from ..pyapi.core import MemorySystem


@dataclass
class QueryResult:
    """查询结果"""
    type: str  # select, insert, update, delete
    data: List[Dict[str, Any]] = None  # 查询结果数据
    affected_rows: int = 0  # 影响的行数
    memory_ids: List[str] = None  # 涉及的 memory_id 列表
    message: str = ""


class Interpreter:
    """
    MQL 解释器

    绑定 MemorySystem，通过 DSL 表达式操作记忆。

    示例：
        inter = Interpreter()
        inter.bind("mem", memory_system)
        result = inter.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")
    """

    def __init__(self):
        self._context: Dict[str, Any] = {}
        # 命名视图注册表（DEFINE 语句创建）
        self._views: Dict[str, str] = {}

    def bind(self, name: str, obj: Any) -> "Interpreter":
        """绑定对象到命名空间"""
        self._context[name] = obj
        return self

    def unbind(self, name: str) -> bool:
        """解除绑定"""
        if name in self._context:
            del self._context[name]
            return True
        return False

    def set_auth_context(self, auth_context: "AuthContext") -> "Interpreter":
        """设置认证上下文"""
        self._context["auth"] = auth_context
        return self

    def _get_auth_context(self) -> Optional["AuthContext"]:
        """获取认证上下文"""
        return self._context.get("auth")

    def _check_permission(self, system_name: str, permission: str) -> bool:
        """检查权限"""
        auth = self._get_auth_context()
        if auth is None:
            return True  # 无认证上下文时放行
        from ..user_manager import PermissionType
        try:
            perm = PermissionType(permission)
        except ValueError:
            perm = PermissionType.READ  # fallback
        return auth.has_permission(system_name, perm)

    def _check_system_access(self, system_name: str, require_write: bool = False) -> bool:
        """检查系统访问权限"""
        auth = self._get_auth_context()
        if auth is None:
            return True
        return auth.has_system_access(system_name, require_write)

    @staticmethod
    def validate_mql(sql: str) -> tuple[bool, str]:
        """
        语法检查 MQL 语句（不执行），返回 (是否合法, 错误信息)。

        用于在 NL Pipeline 中提前发现 LLM 生成的语法错误。
        """
        import re
        from .parser import parse
        from .errors import MQLError

        sql_stripped = sql.strip()

        # 常见语法错误的快速检测（给出友好提示）
        upper = sql_stripped.upper()
        if re.search(r'\bAND\s+TIME\b', upper):
            return False, "语法错误：TIME 是独立子句，不能用 AND 连接。正确：WHERE subject='X' TIME year(2026)，错误：WHERE subject='X' AND TIME year(2026)"
        if re.search(r'\bWHERE\s+\w+\s+LIKE\s+[\'"]\d{4}', upper):
            return False, "语法错误：不要用 LIKE 做时间过滤。用 TIME 关键字：TIME year(2026) AND month(04) AND day(25)"
        if re.search(r'\bBETWEEN\b', upper):
            return False, "语法错误：MQL 不支持 BETWEEN。用 TIME 范围语法：TIME year(2024 TO 2025)"
        if re.search(r'\bORDER\s+BY\b', upper):
            return False, "语法错误：MQL 不支持 ORDER BY。用 TOPK n 按向量匹配度排序"
        if re.search(r'\bGROUP\s+BY\b', upper):
            return False, "语法错误：MQL 不支持 GROUP BY"
        if re.search(r'\bJOIN\b', upper):
            return False, "语法错误：MQL 不支持 JOIN。用 GRAPH 关键字做多跳关联"

        try:
            parse(sql_stripped)
            return True, ""
        except MQLError as e:
            return False, str(e)
        except Exception as e:
            return False, f"未知语法错误: {e}"

    def execute(self, sql: str) -> QueryResult:
        """
        执行 MQL 语句

        Args:
            sql: MQL 语句

        Returns:
            QueryResult
        """
        from .parser import parse

        ast = parse(sql)
        return self._execute_ast(ast)

    def _execute_ast(self, ast: ASTNode) -> QueryResult:
        """执行 AST"""
        if isinstance(ast, SelectStatement):
            return self._execute_select(ast)
        elif isinstance(ast, InsertStatement):
            return self._execute_insert(ast)
        elif isinstance(ast, UpdateStatement):
            return self._execute_update(ast)
        elif isinstance(ast, DeleteStatement):
            return self._execute_delete(ast)
        elif isinstance(ast, DefineStatement):
            return self._execute_define(ast)
        else:
            raise ExecutionError(f"Unknown AST type: {type(ast)}")

    # 六个标准槽位
    _SLOTS = ("scene", "subject", "action", "object", "purpose", "result")

    def _any_slot_contains(self, memory: Dict, keyword: str) -> bool:
        """检查记忆任意槽位是否包含 keyword（子串匹配）"""
        kw = self._strip_quotes(keyword)
        for slot in self._SLOTS:
            if kw in (memory.get(slot, "") or ""):
                return True
        return False

    def _get_memory_system(self) -> "MemorySystem":
        """获取默认的记忆系统"""
        if "mem" in self._context:
            return self._context["mem"]
        if "memory" in self._context:
            return self._context["memory"]
        if len(self._context) > 0:
            return next(iter(self._context.values()))
        raise ExecutionError("No memory system bound. Use bind('mem', memory_system) first.")

    def _parse_query_sentence(self, query_sentence: str) -> Dict[str, str]:
        """解析查询句为槽位字典"""
        parts, current, in_bracket = [], "", False
        for ch in query_sentence:
            if ch == "<":
                in_bracket = True
                current = ""
            elif ch == ">":
                in_bracket = False
                parts.append(current)
            elif in_bracket:
                current += ch

        slot_names = ["scene", "subject", "action", "object", "purpose", "result"]
        result = {}
        for i, name in enumerate(slot_names):
            if i < len(parts):
                result[name] = parts[i]
        return result

    def _strip_quotes(self, value: Any) -> Any:
        """去掉字符串两端的引号"""
        if isinstance(value, str) and len(value) >= 2:
            if (value.startswith("'") and value.endswith("'")) or \
               (value.startswith('"') and value.endswith('"')):
                return value[1:-1]
        return value

    def _evaluate_condition(self, condition: Condition, memory: Dict) -> bool:
        """评估条件是否满足"""
        field = condition.field
        operator = condition.operator
        value = self._strip_quotes(condition.value)

        # ── 跨槽位 bare string 条件：field="" 表示匹配任意槽位 ──
        if field == "" and operator == "any":
            return self._any_slot_contains(memory, condition.value)

        # 获取字段值
        if field in ["scene", "subject", "action", "object", "purpose", "result"]:
            # 槽位值直接存在 memory dict 中（如 memory["subject"]）
            field_value = memory.get(field, "")
        else:
            field_value = memory.get(field)

        # 比较
        if operator == "=" or operator == "==":
            return field_value == value
        elif operator == "!=":
            return field_value != value
        elif operator == "<":
            return field_value < value
        elif operator == ">":
            return field_value > value
        elif operator == "<=":
            return field_value <= value
        elif operator == ">=":
            return field_value >= value
        elif operator.lower() == "like":
            # 简单的前缀匹配
            if value.endswith("%"):
                return str(field_value or "").startswith(value[:-1])
            elif value.startswith("%"):
                return str(field_value or "").endswith(value[1:])
            return value in str(field_value or "")
        elif operator.lower() == "in":
            return field_value in value

        return False

    def _execute_select(self, stmt: SelectStatement) -> QueryResult:
        """执行 SELECT"""
        # 处理 WRAP 包装语法
        if stmt.wrapped_sql:
            return self._execute_wrapped(stmt.wrapped_sql)

        mem = self._get_memory_system()
        results = []

        # 如果有向量搜索条件
        if stmt.search_slots:
            # 使用向量搜索
            search_results = mem.search_subspace(
                query_slots=stmt.search_slots,
                top_k=stmt.search_topk,
            )
            memory_ids = [sr.memory_id for sr in search_results]
        else:
            # 获取所有记忆
            all_ids = mem.list_all_memory_ids()
            memory_ids = all_ids

        # 过滤和返回
        for mid in memory_ids:
            memory = mem.get_memory(mid)
            if memory is None:
                continue

            # 构建内存字典（包含解析后的槽位）
            mem_dict = {
                "id": memory.id,
                "query_sentence": memory.query_sentence,
                "content": memory.content,
                "lifecycle": memory.lifecycle,
                "created_at": str(memory.created_at),
                "updated_at": str(memory.updated_at),
            }

            # 解析槽位
            slots = self._parse_query_sentence(memory.query_sentence)
            for k, v in slots.items():
                mem_dict[k] = v

            # 版本控制
            if stmt.version is not None:
                vm = mem._vec_db.version_manager
                version = vm.get_version(mid, stmt.version)
                if version:
                    mem_dict["content"] = version.content
                    mem_dict["lifecycle"] = version.lifecycle
                    mem_dict["query_sentence"] = version.query_sentence

            # 应用条件过滤
            if stmt.conditions:
                match = all(self._evaluate_condition(c, mem_dict) for c in stmt.conditions)
                if not match:
                    continue

            # 选择字段
            if "*" not in stmt.fields:
                mem_dict = {k: mem_dict[k] for k in stmt.fields if k in mem_dict}

            results.append(mem_dict)

        # 执行 GRAPH 子句（固定位置，在 TIME/TOPK/LIMIT 之前）
        if stmt.graph_clause and results:
            results = self._execute_graph_clause(
                stmt.graph_clause,
                [r.get("id") for r in results if r.get("id")],
                mem,
            )

        # 按 ops 顺序执行 TIME / TOPK / LIMIT
        for op_type, op_value in stmt.ops:
            if op_type == "time" and results:
                results = self._execute_time_filter(op_value, results)
            elif op_type == "topk" and results:
                results = self._execute_topk(op_value, results, mem)
            elif op_type == "limit" and results:
                results = results[:op_value]

        return QueryResult(
            type="select",
            data=results,
            affected_rows=len(results),
            memory_ids=[r.get("id") for r in results],
        )

    def _execute_insert(self, stmt: InsertStatement) -> QueryResult:
        """执行 INSERT"""
        mem = self._get_memory_system()

        # 去掉引号
        query_sentence = self._strip_quotes(stmt.query_sentence)
        content = self._strip_quotes(stmt.content)

        # reference_duration=None 时由 LifecycleManager 决定（用默认 86400）
        write_kwargs = {"query_sentence": query_sentence, "content": content}
        if stmt.reference_duration is not None:
            write_kwargs["reference_duration"] = stmt.reference_duration

        mid = mem.write(**write_kwargs)

        return QueryResult(
            type="insert",
            affected_rows=1,
            memory_ids=[mid],
            message=f"Inserted memory: {mid}",
        )

    def _execute_update(self, stmt: UpdateStatement) -> QueryResult:
        """执行 UPDATE"""
        mem = self._get_memory_system()

        # 先查询要更新的记忆
        select_result = self._execute_select(SelectStatement(
            fields=["id"],
            conditions=stmt.conditions,
        ))

        updated = 0
        updated_ids = []
        for row in select_result.data:
            mid = row["id"]
            # 构建更新参数（去掉引号）
            updates = {}
            for k, v in stmt.updates.items():
                updates[k] = self._strip_quotes(v)
            ok = mem.update(
                memory_id=mid,
                content=updates.get("content"),
                lifecycle=updates.get("lifecycle"),
            )
            if ok:
                updated += 1
                updated_ids.append(mid)

        return QueryResult(
            type="update",
            affected_rows=updated,
            memory_ids=updated_ids,
            message=f"Updated {updated} memories",
        )

    def _execute_delete(self, stmt: DeleteStatement) -> QueryResult:
        """执行 DELETE"""
        mem = self._get_memory_system()

        # 先查询要删除的记忆
        select_result = self._execute_select(SelectStatement(
            fields=["id"],
            conditions=stmt.conditions,
        ))

        deleted = 0
        deleted_ids = []
        for row in select_result.data:
            mid = row["id"]
            if mem.delete(mid):
                deleted += 1
                deleted_ids.append(mid)

        return QueryResult(
            type="delete",
            affected_rows=deleted,
            memory_ids=deleted_ids,
            message=f"Deleted {deleted} memories",
        )

    # =========================================================================
    # 图查询执行
    # =========================================================================

    def _execute_graph_clause(
        self,
        clause: GraphClause,
        seed_memory_ids: List[str],
        mem,
    ) -> List[Dict[str, Any]]:
        """执行 GRAPH 子句，对已有记忆 ID 列表做图扩展

        Args:
            clause: GraphClause AST 节点
            seed_memory_ids: 起始记忆 ID 列表
            mem: MemorySystem 实例

        Returns:
            扩展后的记忆字典列表
        """
        try:
            from ..graph_query import MemoryGraph
        except ImportError:
            return []

        graph = MemoryGraph(mem._vec_db)
        expanded_ids = set()

        for mid in seed_memory_ids:
            if clause.operation == "EXPAND":
                # 获取多跳邻居
                connected = graph.get_connected_component(mid, min_shared_slots=clause.min_shared)
                for gsr in connected:
                    if gsr.distance <= clause.hops and gsr.distance > 0:
                        expanded_ids.add(gsr.memory_id)

            elif clause.operation == "NEIGHBORS":
                # 获取直接邻居
                neighbors = graph.get_neighbors(mid, max_distance=1, min_shared_slots=clause.min_shared)
                for gsr in neighbors:
                    expanded_ids.add(gsr.memory_id)

            elif clause.operation == "PATH":
                # 查找到目标记忆的路径
                if clause.target_id:
                    path = graph.find_path(mid, clause.target_id, max_depth=clause.hops or 3, min_shared_slots=clause.min_shared)
                    if path:
                        for path_id in path:
                            expanded_ids.add(path_id)

            elif clause.operation == "CONNECTED":
                # 获取连通分量
                connected = graph.get_connected_component(mid, min_shared_slots=clause.min_shared)
                for gsr in connected:
                    if gsr.distance > 0:
                        expanded_ids.add(gsr.memory_id)

            elif clause.operation == "VALUE_CHAIN":
                # 沿槽位值链扩展
                if clause.value_slots:
                    chain_results = graph.find_memories_by_value_chain(
                        mid,
                        value_slots=clause.value_slots,
                        max_depth=clause.hops or 3,
                    )
                    for gsr in chain_results:
                        expanded_ids.add(gsr.memory_id)

        # 去重并获取记忆详情
        results = []
        for eid in expanded_ids:
            memory = mem.get_memory(eid)
            if memory:
                mem_dict = {
                    "id": memory.id,
                    "query_sentence": memory.query_sentence,
                    "content": memory.content,
                    "lifecycle": memory.lifecycle,
                    "created_at": str(memory.created_at),
                    "updated_at": str(memory.updated_at),
                }
                slots = self._parse_query_sentence(memory.query_sentence)
                for k, v in slots.items():
                    mem_dict[k] = v
                results.append(mem_dict)

        return results

    def _execute_time_filter(
        self, clause: TimeFilter, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """执行 TIME 组合时间过滤

        各维度(year/month/day/clock)独立过滤，AND 关系（所有指定维度都必须满足）。
        """
        from datetime import datetime

        def get_mem_dt(mem_dict: Dict[str, Any]) -> datetime:
            """从记忆字典获取 datetime（优先 created_at）"""
            ts = mem_dict.get("created_at") or mem_dict.get("updated_at") or ""
            ts = str(ts)
            if not ts:
                return datetime.min
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(ts[:19], fmt)
                except ValueError:
                    pass
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00").split("+")[0])
            except Exception:
                return datetime.min

        def parse_time(val: str):
            """解析 HH:MM 或 HH:MM:SS 为 (hour, minute)"""
            parts = val.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return h * 60 + m  # 转为分钟数便于比较

        def time_filter(mem_dict: Dict[str, Any]) -> bool:
            dt = get_mem_dt(mem_dict)

            # year
            if clause.year_start is not None or clause.year_end is not None:
                y = dt.year
                ys = clause.year_start
                ye = clause.year_end
                if ye is not None and y > ye:
                    return False
                if ys is not None and y < ys:
                    return False

            # month
            if clause.month_start is not None or clause.month_end is not None:
                m = dt.month
                ms = clause.month_start
                me = clause.month_end
                if me is not None and m > me:
                    return False
                if ms is not None and m < ms:
                    return False

            # day
            if clause.day_start is not None or clause.day_end is not None:
                d = dt.day
                ds = clause.day_start
                de = clause.day_end
                if de is not None and d > de:
                    return False
                if ds is not None and d < ds:
                    return False

            # clock (日内分钟)
            if clause.clock_start is not None or clause.clock_end is not None:
                total_min = dt.hour * 60 + dt.minute
                cs = parse_time(clause.clock_start) if clause.clock_start else None
                ce = parse_time(clause.clock_end) if clause.clock_end else None
                if ce is not None and total_min > ce:
                    return False
                if cs is not None and total_min < cs:
                    return False

            return True

        return [r for r in results if time_filter(r)]

    def _execute_topk(
        self,
        k: int,
        results: List[Dict[str, Any]],
        mem,
    ) -> List[Dict[str, Any]]:
        """执行 TOPK：对已过滤的记忆按向量相似度重新排序，取前 k 条

        使用向量搜索获取匹配度分数，然后按分数降序排列结果。
        """
        if not results:
            return results

        # 构造查询槽位：从已有结果的 query_sentence 提取 subject/action 等共性
        # 或者直接用当前结果集的槽位做向量搜索
        # 最简单策略：把已有记忆 ID 对应的搜索结果按 score 排序
        memory_ids = [r.get("id") for r in results if r.get("id")]
        if not memory_ids:
            return results[:k]

        # 用第一个记忆的 query_sentence 槽位作为查询向量
        first_mem = mem.get_memory(memory_ids[0])
        if not first_mem:
            return results[:k]

        # 解析槽位，构建查询
        slots = self._parse_query_sentence(first_mem.query_sentence)
        if not slots:
            return results[:k]

        # 对每个槽位做向量搜索，取分数
        # 注意：search_subspace 只返回 top_k 结果，我们需要所有结果的分数
        # 用 search_fullspace 可以对所有记忆排序
        try:
            search_results = mem.search_fullspace(
                query_slots=slots,
                top_k=min(k * 3, len(memory_ids) * 2),  # 多取一些再过滤
                embedding_mode="INTEREST",
            )
        except Exception:
            return results[:k]

        # 建立 memory_id -> score 的映射
        score_map = {}
        for sr in search_results:
            score_map[sr.memory_id] = sr.score

        # 给结果附加分数，没有分数的排在最后
        scored_results = []
        for r in results:
            mid = r.get("id")
            score = score_map.get(mid, 0.0)
            r_copy = dict(r)
            r_copy["_topk_score"] = score
            scored_results.append(r_copy)

        # 按分数降序，取前 k
        scored_results.sort(key=lambda x: x["_topk_score"], reverse=True)
        topk_results = scored_results[:k]

        # 去掉辅助字段
        for r in topk_results:
            r.pop("_topk_score", None)

        return topk_results

    # =========================================================================
    # 函数包装执行
    # =========================================================================

    def _execute_define(self, stmt: DefineStatement) -> QueryResult:
        """执行 DEFINE 语句，注册命名视图"""
        self._views[stmt.name] = stmt.sql
        return QueryResult(
            type="define",
            affected_rows=1,
            memory_ids=[],
            message=f"Defined view: {stmt.name}",
        )

    def _execute_wrapped(self, wrapped_sql: str) -> QueryResult:
        """执行 WRAP(...) 包装的 SQL"""
        # 去掉首尾空白
        wrapped_sql = wrapped_sql.strip()
        # 递归执行
        return self.execute(wrapped_sql)

    def execute_script(self, script: str) -> List[QueryResult]:
        """
        执行多行脚本（分号分隔）

        Args:
            script: 多行 MQL 脚本

        Returns:
            各语句的结果列表
        """
        results = []
        for line in script.split(";"):
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            try:
                result = self.execute(line)
                results.append(result)
            except Exception as e:
                results.append(QueryResult(
                    type="error",
                    message=str(e),
                ))
        return results

    def __repr__(self):
        return f"Interpreter(context={list(self._context.keys())})"
