"""
MQL - Memory Query Language 解释器（扩展版）

扩展支持：
- 权限检查（AuthContext）
- 系统管理语句（CREATE DATABASE, LIST DATABASES 等）
- 用户管理语句（CREATE USER, GRANT, REVOKE 等）
"""

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, Callable
from dataclasses import dataclass, field

from .parser import (
    ASTNode, SelectStatement, InsertStatement,
    UpdateStatement, DeleteStatement, Condition
)
from .errors import ExecutionError

if TYPE_CHECKING:
    from ..pyapi.core import MemorySystem, PyAPI
    from ..user_manager import UserManager, AuthContext


@dataclass
class QueryResult:
    """查询结果"""
    type: str  # select, insert, update, delete, system, user
    data: List[Dict[str, Any]] = field(default_factory=list)
    affected_rows: int = 0
    memory_ids: List[str] = field(default_factory=list)
    message: str = ""


class Interpreter:
    """
    MQL 解释器

    绑定 MemorySystem/PyAPI，通过 DSL 表达式操作记忆。
    支持权限检查。

    示例：
        inter = Interpreter()
        inter.bind("mem", memory_system)
        inter.bind("api", pyapi)
        inter.bind("auth", auth_context)
        result = inter.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")
    """

    def __init__(self):
        self._context: Dict[str, Any] = {}

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

    def execute(self, sql: str) -> QueryResult:
        """
        执行 MQL 语句

        Args:
            sql: MQL 语句

        Returns:
            QueryResult
        """
        from .parser import parse

        # 解析 SQL
        ast = parse(sql)

        # 执行
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
        elif isinstance(ast, SystemStatement):
            return self._execute_system(ast)
        elif isinstance(ast, UserStatement):
            return self._execute_user(ast)
        else:
            raise ExecutionError(f"Unknown AST type: {type(ast)}")

    # =========================================================================
    # 权限检查
    # =========================================================================

    def _get_auth_context(self) -> Optional["AuthContext"]:
        """获取认证上下文"""
        return self._context.get("auth")

    def _check_permission(self, system_name: str, permission: str) -> bool:
        """检查权限"""
        auth = self._get_auth_context()
        if auth is None:
            return True  # 无认证上下文时放行
        return auth.has_permission(system_name, permission)

    def _check_system_access(self, system_name: str, require_write: bool = False) -> bool:
        """检查系统访问权限"""
        auth = self._get_auth_context()
        if auth is None:
            return True
        return auth.has_system_access(system_name, require_write)

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _get_memory_system(self) -> "MemorySystem":
        """获取默认的记忆系统"""
        if "mem" in self._context:
            return self._context["mem"]
        if "memory" in self._context:
            return self._context["memory"]
        if len(self._context) > 0:
            return next(iter(self._context.values()))
        raise ExecutionError("No memory system bound. Use bind('mem', memory_system) first.")

    def _get_pyapi(self) -> "PyAPI":
        """获取 PyAPI"""
        if "api" in self._context:
            return self._context["api"]
        raise ExecutionError("No PyAPI bound. Use bind('api', pyapi) first.")

    def _get_user_manager(self) -> "UserManager":
        """获取用户管理器"""
        if "um" in self._context:
            return self._context["um"]
        if "user_manager" in self._context:
            return self._context["user_manager"]
        raise ExecutionError("No UserManager bound. Use bind('um', user_manager) first.")

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
            # 从 query_sentence 解析
            if field == "time":
                field_value = memory.get("_slot_time", "")
            elif field == "subject":
                field_value = memory.get("_slot_subject", "")
            elif field == "action":
                field_value = memory.get("_slot_action", "")
            elif field == "object":
                field_value = memory.get("_slot_object", "")
            elif field == "purpose":
                field_value = memory.get("_slot_purpose", "")
            elif field == "result":
                field_value = memory.get("_slot_result", "")
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

    # =========================================================================
    # 记忆操作
    # =========================================================================

    def _execute_select(self, stmt: SelectStatement) -> QueryResult:
        """执行 SELECT"""
        mem = self._get_memory_system()

        # 权限检查
        if not self._check_permission(mem.name, "read"):
            raise PermissionError(f"No read permission on system '{mem.name}'")

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

        return QueryResult(
            type="select",
            data=results,
            affected_rows=len(results),
            memory_ids=[r.get("id") for r in results],
        )

    def _execute_insert(self, stmt: InsertStatement) -> QueryResult:
        """执行 INSERT"""
        mem = self._get_memory_system()

        # 权限检查
        if not self._check_permission(mem.name, "write"):
            raise PermissionError(f"No write permission on system '{mem.name}'")

        # 去掉引号
        query_sentence = self._strip_quotes(stmt.query_sentence)
        content = self._strip_quotes(stmt.content)

        mid = mem.write(
            query_sentence=query_sentence,
            content=content,
            lifecycle=stmt.lifecycle,
        )

        return QueryResult(
            type="insert",
            affected_rows=1,
            memory_ids=[mid],
            message=f"Inserted memory: {mid}",
        )

    def _execute_update(self, stmt: UpdateStatement) -> QueryResult:
        """执行 UPDATE"""
        mem = self._get_memory_system()

        # 权限检查
        if not self._check_permission(mem.name, "write"):
            raise PermissionError(f"No write permission on system '{mem.name}'")

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

        # 权限检查
        if not self._check_permission(mem.name, "write"):
            raise PermissionError(f"No write permission on system '{mem.name}'")

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
    # 系统管理语句
    # =========================================================================

    def _execute_system(self, stmt: "SystemStatement") -> QueryResult:
        """执行系统管理语句"""
        api = self._get_pyapi()

        if stmt.action == "create":
            # CREATE DATABASE database_name
            if not self._check_permission("*", "admin"):
                raise PermissionError("Admin permission required to create databases")

            system = api.create_system(stmt.target, initialize=True)
            return QueryResult(
                type="system",
                message=f"Created database '{stmt.target}'",
            )

        elif stmt.action == "drop":
            # DROP DATABASE database_name
            if not self._check_permission("*", "admin"):
                raise PermissionError("Admin permission required to drop databases")

            ok = api.delete_system(stmt.target)
            return QueryResult(
                type="system",
                message=f"Dropped database '{stmt.target}'" if ok else f"Database '{stmt.target}' not found",
            )

        elif stmt.action == "list":
            # LIST DATABASES
            systems = api.list_all_systems()
            return QueryResult(
                type="system",
                data=systems,
                affected_rows=len(systems),
                message=f"Found {len(systems)} databases",
            )

        elif stmt.action == "use":
            # USE database_name
            if not self._check_system_access(stmt.target):
                raise PermissionError(f"No permission to access system '{stmt.target}'")

            api.set_active_system(stmt.target)
            return QueryResult(
                type="system",
                message=f"Switched to database '{stmt.target}'",
            )

        else:
            raise ExecutionError(f"Unknown system action: {stmt.action}")


@dataclass
class SystemStatement:
    """系统管理语句"""
    action: str  # create, drop, list, use
    target: str  # 数据库名


@dataclass
class UserStatement:
    """用户管理语句"""
    action: str  # create, drop, grant, revoke, list
    username: str = ""
    target: str = ""  # system_name 或 permission
    permission: str = ""


class InterpreterExtended(Interpreter):
    """
    扩展的 MQL 解释器

    支持额外的管理语句：
    - CREATE DATABASE name
    - DROP DATABASE name
    - LIST DATABASES
    - USE database_name
    - CREATE USER username
    - DROP USER username
    - GRANT permission ON system TO user
    - REVOKE permission ON system FROM user
    - LIST USERS
    """

    def _execute_ast(self, ast: ASTNode) -> QueryResult:
        """执行 AST（扩展版）"""
        if isinstance(ast, SystemStatement):
            return self._execute_system(ast)
        elif isinstance(ast, UserStatement):
            return self._execute_user(ast)
        return super()._execute_ast(ast)

    def _execute_system(self, stmt: SystemStatement) -> QueryResult:
        """执行系统管理语句"""
        api = self._get_pyapi()

        if stmt.action == "create":
            # CREATE DATABASE database_name
            auth = self._get_auth_context()
            if auth and not auth.is_superadmin:
                raise PermissionError("Admin permission required to create databases")

            api.create_system(stmt.target, initialize=True)
            return QueryResult(
                type="system",
                message=f"Created database '{stmt.target}'",
            )

        elif stmt.action == "drop":
            # DROP DATABASE database_name
            auth = self._get_auth_context()
            if auth and not auth.is_superadmin:
                raise PermissionError("Admin permission required to drop databases")

            ok = api.delete_system(stmt.target)
            return QueryResult(
                type="system",
                message=f"Dropped database '{stmt.target}'" if ok else f"Database '{stmt.target}' not found",
            )

        elif stmt.action == "list":
            # LIST DATABASES
            systems = api.list_all_systems()
            # 过滤无权限的系统
            if auth and not auth.is_superadmin:
                allowed = set()
                for sys_name in systems:
                    if auth.has_system_access(sys_name["name"]):
                        allowed.add(sys_name["name"])
                systems = [s for s in systems if s["name"] in allowed]

            return QueryResult(
                type="system",
                data=systems,
                affected_rows=len(systems),
                message=f"Found {len(systems)} databases",
            )

        elif stmt.action == "use":
            # USE database_name
            auth = self._get_auth_context()
            if auth and not auth.has_system_access(stmt.target):
                raise PermissionError(f"No permission to access system '{stmt.target}'")

            api.set_active_system(stmt.target)
            # 绑定新的记忆系统
            self.bind("mem", api.active_system)

            return QueryResult(
                type="system",
                message=f"Switched to database '{stmt.target}'",
            )

        else:
            raise ExecutionError(f"Unknown system action: {stmt.action}")

    def _execute_user(self, stmt: UserStatement) -> QueryResult:
        """执行用户管理语句"""
        um = self._get_user_manager()
        auth = self._get_auth_context()

        if not auth or not auth.is_superadmin:
            raise PermissionError("Admin permission required for user management")

        if stmt.action == "create":
            # CREATE USER username
            ok, result = um.create_user(stmt.username)
            if ok:
                return QueryResult(
                    type="user",
                    message=f"Created user '{stmt.username}'",
                    data=[{"username": stmt.username, "api_key": result}],
                )
            else:
                return QueryResult(
                    type="user",
                    message=f"Failed to create user: {result}",
                )

        elif stmt.action == "drop":
            # DROP USER username
            ok, msg = um.delete_user(stmt.username)
            return QueryResult(
                type="user",
                message=msg,
            )

        elif stmt.action == "list":
            # LIST USERS
            users = um.list_users()
            return QueryResult(
                type="user",
                data=users,
                affected_rows=len(users),
                message=f"Found {len(users)} users",
            )

        elif stmt.action == "grant":
            # GRANT permission ON system TO user
            from ..user_manager import PermissionType
            ok, msg = um.grant_permission(
                username=stmt.username,
                system_name=stmt.target,
                permission=PermissionType(stmt.permission),
                granted_by=auth.username,
            )
            return QueryResult(
                type="user",
                message=msg,
            )

        elif stmt.action == "revoke":
            # REVOKE permission ON system FROM user
            from ..user_manager import PermissionType
            ok, msg = um.revoke_permission(
                username=stmt.username,
                system_name=stmt.target,
                permission=PermissionType(stmt.permission),
            )
            return QueryResult(
                type="user",
                message=msg,
            )

        elif stmt.action == "generate_key":
            # GENERATE KEY FOR user
            new_key = um.generate_api_key(stmt.username)
            return QueryResult(
                type="user",
                data=[{"username": stmt.username, "api_key": new_key}],
                message=f"Generated new APIKey for '{stmt.username}'",
            )

        else:
            raise ExecutionError(f"Unknown user action: {stmt.action}")


# 原有的 Interpreter 别名
Interpreter = InterpreterExtended


# ============================================================================
# 注册扩展语句到 Parser
# ============================================================================

def _register_extended_statements():
    """注册扩展语句类型"""
    import MQL.parser as parser_module

    # 添加扩展语句类型到 parser
    # 注意：这里需要确保 parser 支持这些语句
    pass