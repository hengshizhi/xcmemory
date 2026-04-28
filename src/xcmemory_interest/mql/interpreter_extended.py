"""
MQL - Memory Query Language 解释器（扩展版）

扩展支持：
- 权限检查（AuthContext）
- 系统管理语句（CREATE DATABASE, LIST DATABASES 等）
- 用户管理语句（CREATE USER, GRANT, REVOKE 等）
"""

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, Callable

from .parser import (
    ASTNode, SelectStatement, InsertStatement,
    UpdateStatement, DeleteStatement, Condition,
    SystemStatement, UserStatement, SnapshotStatement
)
from .errors import ExecutionError
from .interpreter import Interpreter, QueryResult

if TYPE_CHECKING:
    from ..pyapi.core import MemorySystem, PyAPI
    from ..user_manager import UserManager, AuthContext


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

    def _get_pyapi(self) -> "PyAPI":
        """获取绑定的 PyAPI 实例（由 _exec_mql 绑定到 'api' 键）"""
        api = self._context.get("api")
        if api is None:
            raise ExecutionError("No PyAPI bound. Use bind('api', pyapi_instance) first.")
        return api

    def _execute_ast(self, ast: ASTNode) -> QueryResult:
        """执行 AST（扩展版）"""
        if isinstance(ast, SystemStatement):
            return self._execute_system(ast)
        elif isinstance(ast, UserStatement):
            return self._execute_user(ast)
        elif isinstance(ast, SnapshotStatement):
            return self._execute_snapshot(ast)
        return super()._execute_ast(ast)

    def _execute_insert(self, stmt: InsertStatement) -> QueryResult:
        """执行 INSERT（含权限检查）"""
        mem = self._get_memory_system()
        if not self._check_permission(mem.name, "write"):
            raise PermissionError(f"No write permission on system '{mem.name}'")
        query_sentence = self._strip_quotes(stmt.query_sentence)
        content = self._strip_quotes(stmt.content)
        mid = mem.write(query_sentence=query_sentence, content=content, reference_duration=stmt.lifecycle)
        return QueryResult(
            type="insert",
            affected_rows=1,
            memory_ids=[mid],
            message=f"Inserted memory: {mid}",
        )

    def _execute_system(self, stmt: SystemStatement) -> QueryResult:
        """执行系统管理语句"""
        api = self._get_pyapi()

        if stmt.action == "create":
            # CREATE DATABASE database_name
            auth = self._get_auth_context()
            if auth and not auth.is_superadmin:
                raise PermissionError("Admin permission required to create databases")

            api.create_system(stmt.target, initialize=True)
            # 创建后立即绑定新系统，确保后续语句操作正确的系统
            self.bind("mem", api.active_system)
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
            # 删除后更新绑定（可能是 None 或切到别的系统）
            self.bind("mem", api.active_system)
            return QueryResult(
                type="system",
                message=f"Dropped database '{stmt.target}'" if ok else f"Database '{stmt.target}' not found",
            )

        elif stmt.action == "list":
            # LIST DATABASES
            systems = api.list_all_systems()
            # 过滤无权限的系统
            auth = self._get_auth_context()
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
                system_name=stmt.system_name,
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
                system_name=stmt.system_name,
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

    def _execute_snapshot(self, stmt: SnapshotStatement) -> QueryResult:
        """执行快照管理语句"""
        mem = self._get_memory_system()

        if stmt.action == "create":
            sid = mem.create_snapshot(trigger_reason="manual_mql")
            return QueryResult(
                type="snapshot",
                message=f"Created snapshot '{sid}'",
                data=[{"snapshot_id": sid}],
            )

        elif stmt.action == "list":
            snapshots = mem.list_snapshots()
            return QueryResult(
                type="snapshot",
                data=snapshots,
                message=f"Found {len(snapshots)} snapshot(s)",
            )

        elif stmt.action == "restore":
            if not stmt.snapshot_id:
                raise ExecutionError("RESTORE SNAPSHOT requires a snapshot ID")
            ok = mem.restore_snapshot(stmt.snapshot_id)
            if not ok:
                raise ExecutionError(f"Snapshot '{stmt.snapshot_id}' not found or empty")
            # 恢复后更新绑定
            self.bind("mem", mem)
            return QueryResult(
                type="snapshot",
                message=f"Restored to snapshot '{stmt.snapshot_id}'",
            )

        elif stmt.action == "delete":
            if not stmt.snapshot_id:
                raise ExecutionError("DELETE SNAPSHOT requires a snapshot ID")
            mem.delete_snapshot(stmt.snapshot_id)
            return QueryResult(
                type="snapshot",
                message=f"Deleted snapshot '{stmt.snapshot_id}'",
            )

        else:
            raise ExecutionError(f"Unknown snapshot action: {stmt.action}")


# 原有的 Interpreter 别名
Interpreter = InterpreterExtended


# ============================================================================
# 注册扩展语句到 Parser
# ============================================================================

def _register_extended_statements():
    """注册扩展语句类型（快照等）"""
    # 扩展语句已通过 parser 的 parse 方法内联支持
    pass