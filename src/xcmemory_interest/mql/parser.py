"""
MQL - Memory Query Language 语法分析器

支持的操作：
- SELECT ... FROM memories WHERE ... [VERSION v1] [LIMIT n]
- INSERT INTO memories VALUES (query_sentence, content) 或 (query_sentence, content, reference_duration)
- UPDATE memories SET field=value,... WHERE condition
- DELETE FROM memories WHERE condition
- 向量搜索：WHERE [slot=value,...] SEARCH TOPK n

注意：INSERT 语句中 reference_duration 参数为可选。省略或传入 NULL 时，LifecycleManager 会用
默认参考值 86400 参与生命周期决策（无论 enable_interest_mode 是否启用）。

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

槽位字段：time, subject, action, object, purpose, result
元数据字段：id, content, lifecycle, created_at, updated_at
"""

import re
from typing import List, Optional, Any, Dict, Union
from dataclasses import dataclass, field
from enum import Enum

from .lexer import Lexer, Token, TokenType, tokenize
from .errors import ParseError


# ============================================================================
# AST 节点定义
# ============================================================================

@dataclass
class ASTNode:
    """AST 基础节点"""
    pass


@dataclass
class SelectStatement(ASTNode):
    """SELECT 语句"""
    fields: List[str]  # 字段列表，* 表示所有
    conditions: List["Condition"] = field(default_factory=list)  # WHERE 条件
    version: Optional[int] = None  # VERSION 子句
    limit: Optional[int] = None  # LIMIT 子句
    search_slots: Dict[str, str] = field(default_factory=dict)  # 向量搜索槽位
    search_topk: int = 5  # 搜索 top_k


@dataclass
class InsertStatement(ASTNode):
    """INSERT 语句"""
    query_sentence: str  # 格式: <time><subject><action><object><purpose><result>
    content: str = ""
    lifecycle: Optional[int] = None  # 已废弃，请使用 reference_duration
    reference_duration: Optional[int] = None  # 参考生命周期，由 LifecycleManager 决策（None=用默认值 86400）


@dataclass
class UpdateStatement(ASTNode):
    """UPDATE 语句"""
    updates: Dict[str, Any]  # field: value
    conditions: List["Condition"] = field(default_factory=list)


@dataclass
class DeleteStatement(ASTNode):
    """DELETE 语句"""
    conditions: List["Condition"] = field(default_factory=list)


@dataclass
class Condition:
    """查询条件"""
    field: str
    operator: str  # =, !=, <, >, <=, >=, LIKE, IN
    value: Any


@dataclass
class SystemStatement(ASTNode):
    """系统管理语句"""
    action: str  # create, drop, list, use
    target: str  # 数据库名


@dataclass
class UserStatement(ASTNode):
    """用户管理语句"""
    action: str  # create, drop, grant, revoke, list, generate_key
    username: str = ""
    system_name: str = ""  # for grant/revoke: the system name
    permission: str = ""  # for grant/revoke: the permission type


# ============================================================================
# 解析器
# ============================================================================

class Parser:
    """SQL 风格解析器"""

    SLOT_FIELDS = {"time", "subject", "action", "object", "purpose", "result"}
    META_FIELDS = {"id", "content", "lifecycle", "created_at", "updated_at"}
    ALL_FIELDS = SLOT_FIELDS | META_FIELDS

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token(TokenType.EOF, "")

    def _advance(self) -> Token:
        """移动到下一个 token"""
        token = self.current
        self.pos += 1
        return token

    def _expect(self, token_type: TokenType, value: str = None) -> Token:
        """期望指定类型的 token"""
        token = self.current
        if token.type != token_type:
            raise ParseError(f"Expected {token_type}, got {token.type}", token)
        if value is not None and token.value != value:
            raise ParseError(f"Expected {value!r}, got {token.value!r}", token)
        return self._advance()

    def _match(self, token_type: TokenType, value: str = None) -> bool:
        """检查当前 token 是否匹配"""
        token = self.current
        if token.type != token_type:
            return False
        if value is not None and token.value != value:
            return False
        return True

    def _matchAdvance(self, token_type: TokenType, value: str = None) -> bool:
        """如果匹配则前进"""
        if self._match(token_type, value):
            self._advance()
            return True
        return False

    def _parseFields(self) -> List[str]:
        """解析字段列表"""
        fields = []

        if self._match(TokenType.IDENTIFIER) and self.current.value.lower() == "all":
            self._advance()
            return ["*"]

        while True:
            if self._match(TokenType.IDENTIFIER):
                field_name = self._advance().value.lower()
                if field_name not in self.ALL_FIELDS:
                    raise ParseError(f"Unknown field: {field_name}", self.current)
                fields.append(field_name)
            elif self._match(TokenType.MUL):  # *
                self._advance()
                fields.append("*")
            else:
                break

            if not self._matchAdvance(TokenType.COMMA):
                break

        if not fields:
            fields = ["*"]

        return fields

    def _parseCondition(self) -> Condition:
        """解析单个条件"""
        # 字段
        if self._match(TokenType.IDENTIFIER):
            field = self._advance().value.lower()
        elif self._match(TokenType.LBRACKET):
            # 向量搜索槽位模式: [slot=value, ...]
            self._advance()
            field = "search"
        else:
            raise ParseError("Expected field name", self.current)

        # 运算符
        if self._match(TokenType.LT):
            op = self._advance().value
        elif self._match(TokenType.GT):
            op = self._advance().value
        elif self._match(TokenType.LE):
            op = self._advance().value
        elif self._match(TokenType.GE):
            op = self._advance().value
        elif self._match(TokenType.NE):
            op = self._advance().value
        elif self._match(TokenType.EQ):
            op = self._advance().value
        elif self._match(TokenType.LIKE):
            op = self._advance().value
        elif self._match(TokenType.IN):
            op = self._advance().value
        else:
            raise ParseError("Expected operator", self.current)

        # 值
        value = None
        if self._match(TokenType.STRING):
            value = self._advance().value
            # 处理查询句 <...>
            if value.startswith("<") and not value.endswith(">"):
                # 收集剩余部分构建完整查询句
                parts = [value]
                while self.current.type != TokenType.RBRACKET and self.current.type != TokenType.EOF:
                    if self._match(TokenType.STRING):
                        parts.append(self._advance().value)
                    else:
                        break
                value = "".join(parts)
        elif self._match(TokenType.NUMBER):
            num_str = self._advance().value
            value = float(num_str) if "." in num_str else int(num_str)
        elif self._match(TokenType.NULL):
            self._advance()
            value = None
        elif self._match(TokenType.TRUE):
            self._advance()
            value = True
        elif self._match(TokenType.FALSE):
            self._advance()
            value = False
        elif self._match(TokenType.LBRACKET):
            # 列表值 [1, 2, 3]
            self._advance()
            values = []
            while not self._match(TokenType.RBRACKET):
                if self._match(TokenType.NUMBER):
                    values.append(self._advance().value)
                elif self._match(TokenType.STRING):
                    values.append(self._advance().value)
                else:
                    break
                if not self._matchAdvance(TokenType.COMMA):
                    break
            self._expect(TokenType.RBRACKET)
            value = values

        return Condition(field=field, operator=op, value=value)

    def _parseConditions(self) -> List[Condition]:
        """解析 WHERE 条件（支持 AND/OR）"""
        conditions = []

        while True:
            # 处理 NOT
            if self._matchAdvance(TokenType.NOT):
                cond = self._parseCondition()
                # NOT 的实现：反转运算符
                pass  # 简化处理

            cond = self._parseCondition()
            conditions.append(cond)

            if not self._match(TokenType.AND):
                break
            self._advance()

        return conditions

    def _parseSearchClause(self) -> tuple:
        """解析 [slot=value,...] SEARCH TOPK n"""
        search_slots = {}

        if self._match(TokenType.LBRACKET):
            self._advance()
            # 解析槽位
            while not self._match(TokenType.RBRACKET):
                if self._match(TokenType.IDENTIFIER):
                    slot_name = self._advance().value.lower()
                    if slot_name not in self.SLOT_FIELDS:
                        raise ParseError(f"Unknown slot: {slot_name}", self.current)
                    self._expect(TokenType.EQ)
                    if self._match(TokenType.STRING):
                        slot_value = self._advance().value
                    else:
                        raise ParseError("Expected string value", self.current)
                    search_slots[slot_name] = slot_value
                else:
                    raise ParseError("Expected slot name", self.current)

                if not self._matchAdvance(TokenType.COMMA):
                    break

            self._expect(TokenType.RBRACKET)

        # SEARCH 关键字
        if self._matchAdvance(TokenType.SEARCH):
            pass  # search_slots 已经解析

        # TOPK n
        topk = 5
        if self._matchAdvance(TokenType.TOPK):
            if self._match(TokenType.NUMBER):
                topk = int(self._advance().value)
            else:
                raise ParseError("Expected number after TOPK", self.current)

        return search_slots, topk

    def _parseLimit(self) -> Optional[int]:
        """解析 LIMIT n"""
        if self._matchAdvance(TokenType.LIMIT):
            if self._match(TokenType.NUMBER):
                return int(self._advance().value)
            raise ParseError("Expected number after LIMIT", self.current)
        return None

    def _parseVersion(self) -> Optional[int]:
        """解析 VERSION v1"""
        if self._matchAdvance(TokenType.VERSION):
            if self._match(TokenType.NUMBER):
                return int(self._advance().value)
            if self._match(TokenType.IDENTIFIER):
                # v1 格式
                v_str = self._advance().value
                if v_str.lower().startswith("v"):
                    return int(v_str[1:])
                raise ParseError("Expected version like v1", self.current)
            raise ParseError("Expected version number", self.current)
        return None

    def parse(self) -> ASTNode:
        """解析 SQL 语句"""
        if self._matchAdvance(TokenType.SELECT):
            return self._parseSelect()
        elif self._matchAdvance(TokenType.INSERT):
            return self._parseInsert()
        elif self._matchAdvance(TokenType.UPDATE):
            return self._parseUpdate()
        elif self._matchAdvance(TokenType.DELETE):
            return self._parseDelete()
        elif self._matchAdvance(TokenType.CREATE):
            return self._parseCreate()
        elif self._matchAdvance(TokenType.DROP):
            return self._parseDrop()
        elif self._matchAdvance(TokenType.LIST):
            return self._parseList()
        elif self._matchAdvance(TokenType.USE):
            return self._parseUse()
        elif self._matchAdvance(TokenType.USER):
            return self._parseUserStatement()
        elif self._matchAdvance(TokenType.GRANT):
            return self._parseGrant()
        elif self._matchAdvance(TokenType.REVOKE):
            return self._parseRevoke()
        elif self._matchAdvance(TokenType.GENERATE):
            return self._parseGenerateKey()
        else:
            raise ParseError(f"Expected SQL statement, got {self.current.type}", self.current)

    def _parseSelect(self) -> SelectStatement:
        """解析 SELECT 语句"""
        # 字段
        fields = self._parseFields()

        # FROM
        self._expect(TokenType.FROM)
        self._expect(TokenType.IDENTIFIER)  # memories

        # WHERE
        conditions = []
        search_slots = {}
        search_topk = 5

        if self._matchAdvance(TokenType.WHERE):
            # 检查是否是搜索模式
            if self._match(TokenType.LBRACKET):
                search_slots, search_topk = self._parseSearchClause()
            else:
                conditions = self._parseConditions()

        # VERSION
        version = self._parseVersion()

        # LIMIT
        limit = self._parseLimit()

        return SelectStatement(
            fields=fields,
            conditions=conditions,
            version=version,
            limit=limit,
            search_slots=search_slots,
            search_topk=search_topk,
        )

    def _parseInsert(self) -> InsertStatement:
        """解析 INSERT 语句"""
        # INTO
        self._expect(TokenType.INTO)
        self._expect(TokenType.IDENTIFIER)  # memories
        # VALUES
        self._expect(TokenType.VALUES)
        self._expect(TokenType.LPAREN)

        # 解析值
        values = []
        while not self._match(TokenType.RPAREN):
            if self._match(TokenType.STRING):
                values.append(self._advance().value)
            elif self._match(TokenType.NUMBER):
                num_str = self._advance().value
                values.append(float(num_str) if "." in num_str else int(num_str))
            else:
                raise ParseError("Expected value", self.current)

            if not self._matchAdvance(TokenType.COMMA):
                break

        self._expect(TokenType.RPAREN)

        # 解析查询句
        if len(values) >= 1:
            query_sentence = values[0]
        else:
            raise ParseError("INSERT requires query_sentence", self.current)

        content = values[1] if len(values) >= 2 else ""
        reference_duration = values[2] if len(values) >= 3 else None  # None → 让 LifecycleManager 决定（用默认 86400）

        return InsertStatement(
            query_sentence=query_sentence,
            content=content,
            reference_duration=reference_duration,
        )

    def _parseUpdate(self) -> UpdateStatement:
        """解析 UPDATE 语句"""
        # memories
        self._expect(TokenType.IDENTIFIER)

        # SET
        self._expect(TokenType.SET)

        # 更新字段
        updates = {}
        while not self._match(TokenType.WHERE):
            if self._match(TokenType.IDENTIFIER):
                field = self._advance().value.lower()
                self._expect(TokenType.EQ)
                if self._match(TokenType.STRING):
                    updates[field] = self._advance().value
                elif self._match(TokenType.NUMBER):
                    num_str = self._advance().value
                    updates[field] = float(num_str) if "." in num_str else int(num_str)
                elif self._match(TokenType.NULL):
                    self._advance()
                    updates[field] = None
                else:
                    raise ParseError("Expected value", self.current)
            else:
                raise ParseError("Expected field name", self.current)

            if not self._matchAdvance(TokenType.COMMA):
                break

        # WHERE
        conditions = []
        if self._matchAdvance(TokenType.WHERE):
            conditions = self._parseConditions()

        return UpdateStatement(updates=updates, conditions=conditions)

    def _parseDelete(self) -> DeleteStatement:
        """解析 DELETE 语句"""
        # FROM
        self._expect(TokenType.FROM)
        self._expect(TokenType.IDENTIFIER)  # memories

        # WHERE
        conditions = []
        if self._matchAdvance(TokenType.WHERE):
            conditions = self._parseConditions()

        return DeleteStatement(conditions=conditions)

    # =========================================================================
    # 系统管理语句
    # =========================================================================

    def _parseCreate(self) -> ASTNode:
        """解析 CREATE 语句"""
        # CREATE DATABASE name
        if self._matchAdvance(TokenType.DATABASE):
            name = self._expect(TokenType.IDENTIFIER).value
            return SystemStatement(action="create", target=name)
        elif self._matchAdvance(TokenType.USER):
            # CREATE USER username
            username = self._expect(TokenType.IDENTIFIER).value
            return UserStatement(action="create", username=username)
        else:
            raise ParseError("Expected DATABASE or USER after CREATE", self.current)

    def _parseDrop(self) -> ASTNode:
        """解析 DROP 语句"""
        # DROP DATABASE name
        if self._matchAdvance(TokenType.DATABASE):
            name = self._expect(TokenType.IDENTIFIER).value
            return SystemStatement(action="drop", target=name)
        elif self._matchAdvance(TokenType.USER):
            # DROP USER username
            username = self._expect(TokenType.IDENTIFIER).value
            return UserStatement(action="drop", username=username)
        else:
            raise ParseError("Expected DATABASE or USER after DROP", self.current)

    def _parseList(self) -> ASTNode:
        """解析 LIST 语句"""
        # LIST DATABASES
        if self._matchAdvance(TokenType.DATABASES) or self._matchAdvance(TokenType.SYSTEMS):
            return SystemStatement(action="list", target="")
        # LIST USERS
        elif self._matchAdvance(TokenType.USERS):
            return UserStatement(action="list")
        else:
            raise ParseError("Expected DATABASES or USERS after LIST", self.current)

    def _parseUse(self) -> SystemStatement:
        """解析 USE 语句"""
        # USE database_name
        name = self._expect(TokenType.IDENTIFIER).value
        return SystemStatement(action="use", target=name)

    # =========================================================================
    # 用户管理语句
    # =========================================================================

    def _parseUserStatement(self) -> UserStatement:
        """解析 USER 语句"""
        # USER username (used after CREATE/DROP, but here we handle standalone USER for LIST USERS)
        if self._matchAdvance(TokenType.USERS):
            return UserStatement(action="list")
        username = self._expect(TokenType.IDENTIFIER).value
        return UserStatement(action="create", username=username)

    def _parseGrant(self) -> UserStatement:
        """解析 GRANT 语句"""
        # GRANT permission ON system TO user
        # permission: read, write, read_write, admin, version_commit, version_delete
        perm_token = self._advance()
        if perm_token.type not in (TokenType.IDENTIFIER, TokenType.READ, TokenType.WRITE, TokenType.ADMIN):
            raise ParseError("Expected permission type after GRANT", self.current)
        permission = perm_token.value.lower()

        self._expect(TokenType.ON)
        system_name = self._expect(TokenType.IDENTIFIER).value

        self._expect(TokenType.TO)
        username = self._expect(TokenType.IDENTIFIER).value

        return UserStatement(action="grant", username=username, system_name=system_name, permission=permission)

    def _parseRevoke(self) -> UserStatement:
        """解析 REVOKE 语句"""
        # REVOKE permission ON system FROM user
        perm_token = self._advance()
        if perm_token.type not in (TokenType.IDENTIFIER, TokenType.READ, TokenType.WRITE, TokenType.ADMIN):
            raise ParseError("Expected permission type after REVOKE", self.current)
        permission = perm_token.value.lower()

        self._expect(TokenType.ON)
        system_name = self._expect(TokenType.IDENTIFIER).value

        self._expect(TokenType.FROM)
        username = self._expect(TokenType.IDENTIFIER).value

        return UserStatement(action="revoke", username=username, system_name=system_name, permission=permission)

    def _parseGenerateKey(self) -> UserStatement:
        """解析 GENERATE KEY 语句"""
        # GENERATE KEY FOR user
        self._expect(TokenType.KEY)
        self._expect(TokenType.FOR)
        username = self._expect(TokenType.IDENTIFIER).value
        return UserStatement(action="generate_key", username=username)


def parse(sql: str) -> ASTNode:
    """便捷函数：解析 SQL 语句"""
    tokens = tokenize(sql)
    parser = Parser(tokens)
    return parser.parse()
