"""
MQL - Memory Query Language 解释器

执行 DSL 语句，操作记忆系统
"""

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING
from dataclasses import dataclass

from .parser import (
    ASTNode, SelectStatement, InsertStatement,
    UpdateStatement, DeleteStatement, Condition,
    DefineStatement, GraphClause
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

        slot_names = ["time", "subject", "action", "object", "purpose", "result"]
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

        # 获取字段值
        if field in ["time", "subject", "action", "object", "purpose", "result"]:
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

        # LIMIT
        if stmt.limit:
            results = results[:stmt.limit]

        # 执行 GRAPH 子句
        if stmt.graph_clause and results:
            results = self._execute_graph_clause(
                stmt.graph_clause,
                [r.get("id") for r in results if r.get("id")],
                mem,
            )

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
