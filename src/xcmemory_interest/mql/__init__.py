"""
MQL - Memory Query Language

记忆查询语言：类 SQL 的字符串操作接口

支持的操作：
- SELECT ... FROM memories WHERE ... [VERSION v1] [LIMIT n]
- INSERT INTO memories VALUES (query_sentence, content, lifecycle)
- UPDATE memories SET field=value,... WHERE condition
- DELETE FROM memories WHERE condition
- 向量搜索：SELECT * FROM memories WHERE [slot=value,...] SEARCH TOPK n

系统管理：
- CREATE DATABASE name
- DROP DATABASE name
- LIST DATABASES
- USE database_name

用户管理：
- CREATE USER username
- DROP USER username
- LIST USERS
- GRANT permission ON system TO user
- REVOKE permission ON system FROM user
- GENERATE KEY FOR user

示例：
    from xcmemory_interest.mql import Interpreter

    inter = Interpreter()
    inter.bind("mem", memory_system)
    inter.bind("api", pyapi)
    inter.bind("um", user_manager)

    # 查询
    result = inter.execute("SELECT * FROM memories WHERE subject='我' LIMIT 5")

    # 插入
    result = inter.execute(
        "INSERT INTO memories VALUES ('<平时><我><学><编程><喜欢><有收获>', '我喜欢学编程', 86400)"
    )

    # 创建数据库
    result = inter.execute("CREATE DATABASE my_system")

    # 用户管理
    result = inter.execute("CREATE USER alice")
    result = inter.execute("GRANT read ON my_system TO alice")
"""

__version__ = "0.2.0"

from .errors import MQLError, LexerError, ParseError, ExecutionError, ValidationError
from .lexer import Lexer, Token, TokenType, tokenize
from .parser import (
    parse, ASTNode,
    SelectStatement, InsertStatement, UpdateStatement, DeleteStatement,
    Condition, SystemStatement, UserStatement, SnapshotStatement
)
from .interpreter_extended import Interpreter, QueryResult
from .dryrun import DryRunInterpreter, DryRunMixIn
from .time_filter import TimeFilterMixIn, RELATIVE_TIME_MAP, parse_relative_time
from .sto_operations import STOOperations

__all__ = [
    # 版本
    "__version__",
    # 错误
    "MQLError",
    "LexerError",
    "ParseError",
    "ExecutionError",
    "ValidationError",
    # 词法
    "Lexer",
    "Token",
    "TokenType",
    "tokenize",
    # 语法
    "parse",
    "ASTNode",
    "SelectStatement",
    "InsertStatement",
    "UpdateStatement",
    "DeleteStatement",
    "Condition",
    "SystemStatement",
    "UserStatement",
    "SnapshotStatement",
    # 解释器
    "Interpreter",
    "QueryResult",
    # Dry-run 模式
    "DryRunInterpreter",
    "DryRunMixIn",
    # 相对时间过滤器
    "TimeFilterMixIn",
    "RELATIVE_TIME_MAP",
    "parse_relative_time",
    # STO 操作集
    "STOOperations",
]