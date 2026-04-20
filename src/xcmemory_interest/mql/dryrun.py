"""
Dry-run 模式：为 MQL Interpreter 增加预览能力

支持语法：
    DELETE FROM memories WHERE time < '2024-01-01' DRYRUN
    DELETE FROM memories WHERE subject='我' DRY RUN

在 dry_run 模式下，DELETE 语句会先预览影响范围（影响行数、memory_ids），
但不实际执行删除操作。
"""

from .parser import DeleteStatement, SelectStatement, parse
from .interpreter_extended import InterpreterExtended as BaseInterpreterExtended, QueryResult


class DryRunMixIn:
    """
    为 InterpreterExtended 增加 Dry-run 能力的 Mixin

    使用方式：
        class MyInterpreter(DryRunMixIn, BaseInterpreterExtended):
            pass

        inter = MyInterpreter()
        inter.bind("mem", memory_system)

        # Dry-run 预览（不实际删除）
        result = inter.execute_with_dryrun(
            "DELETE FROM memories WHERE time < '2024-01-01' DRYRUN"
        )
        print(result.message)  # "将删除 42 条记忆（dry_run）"

        # 实际执行删除
        result = inter.execute_with_dryrun(
            "DELETE FROM memories WHERE time < '2024-01-01'"
        )
        print(result.message)  # "Deleted 42 memories"
    """

    def execute_with_dryrun(self, sql: str) -> QueryResult:
        """
        执行 MQL，支持 dryrun 模式

        自动检测语句是否携带 DRYRUN/DRY RUN 修饰符。

        Args:
            sql: MQL 语句，如 "DELETE FROM memories WHERE time < '2024-01-01' DRYRUN"

        Returns:
            QueryResult
            - dry_run=True 时：type="delete_preview"，affected_rows 为将删除的行数
            - dry_run=False 时：type="delete"，affected_rows 为实际删除的行数
        """
        ast = parse(sql)

        if isinstance(ast, DeleteStatement) and ast.kwargs.get("dry_run"):
            return self._execute_delete_dryrun(ast)

        # 无 dry_run 修饰符，走标准执行流程
        return self.execute(sql)

    def _execute_delete_dryrun(self, stmt: DeleteStatement) -> QueryResult:
        """
        执行 DELETE（dry_run 模式）：只预览，不实际删除

        Args:
            stmt: DeleteStatement（携带 kwargs["dry_run"]=True）

        Returns:
            QueryResult(type="delete_preview", affected_rows=N, memory_ids=[...])
        """
        # 预览：构造只查 id 的 SELECT，利用已有的 _execute_select
        select_stmt = SelectStatement(
            fields=["id"],
            conditions=stmt.conditions,
        )
        select_result = self._execute_select(select_stmt)

        memory_ids = [r.get("id") for r in select_result.data if r.get("id")]

        return QueryResult(
            type="delete_preview",
            data=[{"id": mid} for mid in memory_ids],
            affected_rows=len(memory_ids),
            memory_ids=memory_ids,
            message=f"将删除 {len(memory_ids)} 条记忆（dry_run）",
        )


class DryRunInterpreter(DryRunMixIn, BaseInterpreterExtended):
    """
    支持 Dry-run 的 MQL 解释器

    继承 BaseInterpreterExtended 的全部能力（权限检查、系统管理、用户管理），
    并增加 DELETE 语句的 DRYRUN/DRY RUN 预览能力。

    示例：
        inter = DryRunInterpreter()
        inter.bind("mem", memory_system)
        inter.bind("api", pyapi)
        inter.bind("auth", auth_context)

        # Dry-run 预览
        result = inter.execute_with_dryrun(
            "DELETE FROM memories WHERE subject='我' DRY RUN"
        )
        assert result.type == "delete_preview"
        print(f"将删除 {result.affected_rows} 条记忆，IDs: {result.memory_ids}")

        # 实际执行
        result = inter.execute_with_dryrun(
            "DELETE FROM memories WHERE subject='我'"
        )
        assert result.type == "delete"
    """
    pass
