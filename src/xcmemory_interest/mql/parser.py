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

槽位字段：scene, subject, action, object, purpose, result
元数据字段：id, content, lifecycle, created_at, updated_at
"""

import re
from typing import List, Optional, Any, Dict, Union, Tuple
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
class GraphClause(ASTNode):
    """GRAPH 子句 - 对查询结果执行图扩展操作"""
    operation: str  # EXPAND, NEIGHBORS, PATH, CONNECTED, VALUE_CHAIN
    hops: int = 1  # 扩展跳数（用于 EXPAND/CONNECTED）
    min_shared: int = 1  # 最少共享槽位数
    target_id: Optional[str] = None  # 目标 memory_id（用于 PATH）
    value_slots: Optional[List[str]] = None  # 槽位列表（用于 VALUE_CHAIN）
    limit: Optional[int] = None  # LIMIT 子句（跟在 GRAPH 之后）


@dataclass
class TimeFilter(ASTNode):
    """TIME 组合时间过滤子句

    语法：TIME [year(YEAR OP YEAR | *) [AND month(MONTH OP MONTH | *) [AND day(DAY OP DAY | *) [AND clock(CLOCK OP CLOCK | *)]]]]

    示例：
      TIME year(2024 TO 2025) AND month(01 TO 03)           -- 2024-2025年 且 1-3月
      TIME year(*) AND month(09 TO 12)                        -- 仅限制月份
      TIME year(2024) AND month(01 OR 09) AND day(15)       -- 2024年1月或9月15日
      TIME year(*) AND clock(09:00 TO 18:00)                 -- 仅限制日内时段
    """
    # 各维度过滤条件，None 表示该维度不限制（相当于 *）
    # 单值语法 year(2024) 会被解析为 year_start=2024, year_end=2024
    year_start: Optional[int] = None
    year_end: Optional[int] = None
    month_start: Optional[int] = None
    month_end: Optional[int] = None
    day_start: Optional[int] = None
    day_end: Optional[int] = None
    clock_start: Optional[str] = None  # HH:MM
    clock_end: Optional[str] = None


@dataclass
class SelectStatement(ASTNode):
    """SELECT 语句

    ops 存储 TIME/TOPK/LIMIT 的有序操作列表，按书写顺序执行。
    例如 "TIME ... TOPK ... LIMIT ..." -> ops=[("time", tf), ("topk", k), ("limit", n)]
    """
    fields: List[str]  # 字段列表，* 表示所有
    conditions: List["Condition"] = field(default_factory=list)  # WHERE 条件
    version: Optional[int] = None  # VERSION 子句
    ops: List[Tuple[str, Any]] = field(default_factory=list)  # 有序操作：("time", TimeFilter) | ("topk", int) | ("limit", int)
    time_filter: Optional[TimeFilter] = None  # TIME FROM ... TO ... 子句（兼容，ops 里也有）
    limit: Optional[int] = None  # LIMIT 子句（兼容，ops 里也有）
    topk: Optional[int] = None  # TOPK 子句（兼容，ops 里也有）
    search_slots: Dict[str, str] = field(default_factory=dict)  # 向量搜索槽位
    search_topk: int = 5  # 搜索 top_k
    graph_clause: Optional[GraphClause] = None  # GRAPH 子句
    wrapped_sql: Optional[str] = None  # WRAP(...) 包装的 SQL


@dataclass
class InsertStatement(ASTNode):
    """INSERT 语句"""
    query_sentence: str  # 格式: <scene><subject><action><object><purpose><result>
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
    kwargs: Dict[str, Any] = field(default_factory=dict)  # 支持 dry_run=True 等


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
class SnapshotStatement(ASTNode):
    """快照管理语句"""
    action: str  # create, list, restore, delete
    snapshot_id: str = ""  # for restore/delete


@dataclass
class UserStatement(ASTNode):
    """用户管理语句"""
    action: str  # create, drop, grant, revoke, list, generate_key
    username: str = ""
    system_name: str = ""  # for grant/revoke: the system name
    permission: str = ""  # for grant/revoke: the permission type


@dataclass
class DefineStatement(ASTNode):
    """DEFINE 语句 - 定义命名查询（类似视图）
    
    语法：DEFINE view_name AS SELECT ...
    """
    name: str  # 视图名称
    sql: str  # 完整的 SELECT 语句


# ============================================================================
# 解析器
# ============================================================================

class Parser:
    """SQL 风格解析器"""

    SLOT_FIELDS = {"scene", "subject", "action", "object", "purpose", "result"}
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
        """解析单个条件

        支持两种写法：
        - 显式槽位：WHERE subject='我' AND action='是'
        - 跨槽位关键字：WHERE '恋人' AND '哥哥'   （field="" 表示匹配任意槽位）
        """
        # 字段
        if self._match(TokenType.IDENTIFIER):
            field = self._advance().value.lower()
        elif self._match(TokenType.LBRACKET):
            # 向量搜索槽位模式: [slot=value, ...]
            self._advance()
            field = "search"
        elif self._match(TokenType.STRING):
            # 跨槽位 bare string：field="" 表示任意槽位
            # 语法：WHERE '关键词' AND '另一个词'
            value = self._advance().value
            return Condition(field="", operator="any", value=value)
        else:
            raise ParseError("Expected field name or bare string keyword", self.current)

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
            # 去掉首尾引号
            if (value.startswith("'") and value.endswith("'")) or \
               (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
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
                        # 去掉首尾引号
                        if (slot_value.startswith("'") and slot_value.endswith("'")) or \
                           (slot_value.startswith('"') and slot_value.endswith('"')):
                            slot_value = slot_value[1:-1]
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

    def _parseTopK(self) -> Optional[int]:
        """解析 TOPK n（过滤后按匹配度排序取前 n 条）"""
        if self._matchAdvance(TokenType.TOPK):
            if self._match(TokenType.NUMBER):
                return int(self._advance().value)
            raise ParseError("Expected number after TOPK", self.current)
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

    def _parseTimeFilter(self) -> Optional[TimeFilter]:
        """解析 TIME 组合时间过滤子句

        语法：TIME [year(YEAR [TO YEAR | OR YEAR | *]) [AND month(MONTH [TO MONTH | OR MONTH | *]) [...]]]
        TIME 出现在 SELECT 语句中任意位置（WHERE/VERSION/TIME/LIMIT/TOPK/GRAPH 之后均可），
        按从左到右顺序解析，遇到 LIMIT/TOPK/GRAPH/EOF/SELECT 等语句关键字终止。

        示例：
          TIME year(2024 TO 2025) AND month(01 TO 03)
          TIME year(*) AND month(09 TO 12)
          TIME year(2024) AND clock(09:00 TO 18:00)
          TIME year(*) AND day(15) AND clock(*)
        """
        if not self._match(TokenType.TIME):
            return None
        self._advance()  # 消耗 TIME

        # 如果 TIME 后直接是其他语句关键字，说明是 TIME * （不过滤）
        if self.current.type in (TokenType.LIMIT, TokenType.TOPK, TokenType.GRAPH,
                                  TokenType.SELECT, TokenType.EOF, TokenType.FROM):
            return TimeFilter()  # 全空 = 不过滤

        # 解析第一个维度: year/month/day/clock
        f = TimeFilter()
        dim_parsed = self._parseTimeDimension(f)
        if dim_parsed:
            # 继续解析 AND 连接的后续维度
            while self._matchAdvance(TokenType.AND):
                self._parseTimeDimension(f)

        return f

    def _parseTimeDimension(self, f: TimeFilter) -> bool:
        """解析单个时间维度 year/month/day/clock(...)

        Returns:
            True if a dimension was parsed, False if not
        """
        dim = None
        if self._matchAdvance(TokenType.YEAR):
            dim = "year"
        elif self._matchAdvance(TokenType.MONTH):
            dim = "month"
        elif self._matchAdvance(TokenType.DAY):
            dim = "day"
        elif self._matchAdvance(TokenType.CLOCK):
            dim = "clock"
        else:
            return False

        self._expect(TokenType.LPAREN)

        values = []  # list of (start, end); None means *

        while not self._match(TokenType.RPAREN):
            # * 通配
            if self._match(TokenType.MUL):
                self._advance()
                values = []  # 清空表示通配
                break

            if dim == "clock":
                # clock: HH:MM，可能被拆成 NUMBER COLON NUMBER
                if self._match(TokenType.NUMBER):
                    h = self._advance().value
                    m = "00"
                    if self._match(TokenType.COLON):
                        self._advance()
                        m = self._advance().value
                    start_str = f"{h}:{m}"
                else:
                    raise ParseError(f"Expected time in CLOCK, got {self.current.type}", self.current)
                end_str = start_str
                # 检查是否有 TO range
                if self._match(TokenType.TO):
                    self._advance()
                    if self._match(TokenType.NUMBER):
                        h2 = self._advance().value
                        m2 = "00"
                        if self._match(TokenType.COLON):
                            self._advance()
                            m2 = self._advance().value
                        end_str = f"{h2}:{m2}"
                    else:
                        raise ParseError("Expected time after TO in CLOCK", self.current)
                values.append((start_str, end_str))
            else:
                # year/month/day: 数字 [TO 数字]
                if not self._match(TokenType.NUMBER):
                    raise ParseError(f"Expected number for {dim}, got {self.current.type}", self.current)
                start_val = int(self._advance().value)
                end_val = start_val
                if self._match(TokenType.TO):
                    self._advance()
                    if not self._match(TokenType.NUMBER):
                        raise ParseError(f"Expected end number after TO in {dim}", self.current)
                    end_val = int(self._advance().value)
                values.append((start_val, end_val))

            # OR 分隔多个值/范围
            if not self._matchAdvance(TokenType.OR):
                break

        self._expect(TokenType.RPAREN)

        # 写入对应字段（多值只取第一个）
        if not values:
            pass  # * = 不限制
        else:
            v = values[0]
            if dim == "year":
                f.year_start, f.year_end = v[0], v[1]
            elif dim == "month":
                f.month_start, f.month_end = v[0], v[1]
            elif dim == "day":
                f.day_start, f.day_end = v[0], v[1]
            elif dim == "clock":
                f.clock_start, f.clock_end = v[0], v[1]

        return True

    def _parseGraphClause(self) -> GraphClause:
        """解析 GRAPH 操作子句

        支持格式：
          GRAPH EXPAND(HOPS n)
          GRAPH EXPAND(HOPS n MIN_SHARED m)
          GRAPH NEIGHBORS(MIN_SHARED m)
          GRAPH PATH(TO 'memory_id')
          GRAPH PATH(TO 'memory_id' MIN_SHARED m)
          GRAPH CONNECTED(MIN_SHARED m)
          GRAPH VALUE_CHAIN(SLOTS [slot1, slot2, ...])
        """
        self._expect(TokenType.GRAPH)

        # 操作类型
        if self._matchAdvance(TokenType.EXPAND):
            op = "EXPAND"
        elif self._matchAdvance(TokenType.NEIGHBORS):
            op = "NEIGHBORS"
        elif self._matchAdvance(TokenType.PATH):
            op = "PATH"
        elif self._matchAdvance(TokenType.CONNECTED):
            op = "CONNECTED"
        elif self._matchAdvance(TokenType.VALUE_CHAIN):
            op = "VALUE_CHAIN"
        else:
            # GRAPH 后面直接跟括号也行：GRAPH(EXPAND HOPS 2)
            if self._match(TokenType.LPAREN):
                self._advance()
                if self._matchAdvance(TokenType.EXPAND):
                    op = "EXPAND"
                elif self._matchAdvance(TokenType.NEIGHBORS):
                    op = "NEIGHBORS"
                elif self._matchAdvance(TokenType.PATH):
                    op = "PATH"
                elif self._matchAdvance(TokenType.CONNECTED):
                    op = "CONNECTED"
                elif self._matchAdvance(TokenType.VALUE_CHAIN):
                    op = "VALUE_CHAIN"
                else:
                    raise ParseError(
                        "Expected graph operation: EXPAND, NEIGHBORS, PATH, CONNECTED, VALUE_CHAIN",
                        self.current,
                    )
            else:
                raise ParseError(
                    "Expected graph operation after GRAPH", self.current
                )

        # 解析参数字号
        hops = 1
        min_shared = 1
        target_id = None
        value_slots = None

        if self._match(TokenType.LPAREN):
            self._advance()
            while not self._match(TokenType.RPAREN):
                consumed = False  # 跟踪本轮是否成功消费了 token

                if self._matchAdvance(TokenType.HOPS):
                    consumed = True
                    # 支持 HOPS=2 或 HOPS 2
                    if self._matchAdvance(TokenType.EQ):
                        hops = int(self._expect(TokenType.NUMBER).value)
                    elif self._match(TokenType.NUMBER):
                        hops = int(self._advance().value)
                    else:
                        raise ParseError("Expected number after HOPS", self.current)
                elif self._matchAdvance(TokenType.MIN_SHARED):
                    consumed = True
                    if self._matchAdvance(TokenType.EQ):
                        min_shared = int(self._expect(TokenType.NUMBER).value)
                    elif self._match(TokenType.NUMBER):
                        min_shared = int(self._advance().value)
                    else:
                        raise ParseError("Expected number after MIN_SHARED", self.current)
                elif self._matchAdvance(TokenType.TO):
                    consumed = True
                    if self._match(TokenType.STRING):
                        target_id = self._advance().value
                    else:
                        raise ParseError("Expected memory_id string after TO", self.current)
                elif self._matchAdvance(TokenType.SLOTS):
                    consumed = True
                    # SLOTS [...] 或 SLOTS=[...]
                    if self._matchAdvance(TokenType.EQ):
                        pass  # EQ consumed
                    self._expect(TokenType.LBRACKET)
                    value_slots = []
                    while not self._match(TokenType.RBRACKET):
                        if self._match(TokenType.IDENTIFIER):
                            slot_name = self._advance().value.lower()
                            if slot_name not in self.SLOT_FIELDS:
                                raise ParseError(f"Unknown slot: {slot_name}", self.current)
                            value_slots.append(slot_name)
                        else:
                            raise ParseError("Expected slot name", self.current)
                        if self._matchAdvance(TokenType.COMMA):
                            continue
                        if self._match(TokenType.RBRACKET):
                            break
                        # 非逗号非右括号，跳过
                        self._advance()
                    self._expect(TokenType.RBRACKET)

                if not consumed:
                    raise ParseError(
                        "Expected parameter: HOPS, MIN_SHARED, TO, or SLOTS",
                        self.current,
                    )

                # 逗号分隔，可选；遇到右括号自动退出
                if not self._matchAdvance(TokenType.COMMA):
                    if self._match(TokenType.RPAREN):
                        break
                    # 没逗号也不是右括号，说明是下一个参数，继续循环
            self._expect(TokenType.RPAREN)

        return GraphClause(
            operation=op,
            hops=hops,
            min_shared=min_shared,
            target_id=target_id,
            value_slots=value_slots,
        )

    def parse(self) -> ASTNode:
        """解析 SQL 语句"""
        if self._matchAdvance(TokenType.SELECT):
            return self._parseSelect()
        elif self._matchAdvance(TokenType.INSERT):
            return self._parseInsert()
        elif self._matchAdvance(TokenType.UPDATE):
            return self._parseUpdate()
        elif self._matchAdvance(TokenType.DELETE):
            if self._match(TokenType.SNAPSHOT):
                self._advance()  # consume SNAPSHOT
                snapshot_id = ""
                if self._match(TokenType.IDENTIFIER) or self._match(TokenType.STRING):
                    snapshot_id = self._advance().value
                    if snapshot_id.startswith("'") or snapshot_id.startswith('"'):
                        snapshot_id = snapshot_id[1:-1]
                return SnapshotStatement(action="delete", snapshot_id=snapshot_id)
            return self._parseDelete()
        elif self._matchAdvance(TokenType.RESTORE):
            self._expect(TokenType.SNAPSHOT)
            snapshot_id = ""
            if self._match(TokenType.IDENTIFIER) or self._match(TokenType.STRING):
                snapshot_id = self._advance().value
                if snapshot_id.startswith("'") or snapshot_id.startswith('"'):
                    snapshot_id = snapshot_id[1:-1]
            return SnapshotStatement(action="restore", snapshot_id=snapshot_id)
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
        elif self._matchAdvance(TokenType.DEFINE):
            return self._parseDefine()
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
                # 条件解析后可能还跟着 SEARCH（WHERE subject='我' SEARCH ...）
                if self._match(TokenType.SEARCH):
                    self._advance()
                    # 把已有的等值条件转换为搜索槽位
                    for cond in conditions:
                        if cond.operator in ("=", "==") and cond.field in self.SLOT_FIELDS:
                            search_slots[cond.field] = str(cond.value)
                    # 条件清空（已转为搜索槽位）
                    if search_slots:
                        conditions = []
                    # 解析 SEARCH 后的 TOPK
                    if self._matchAdvance(TokenType.TOPK):
                        if self._match(TokenType.NUMBER):
                            search_topk = int(self._advance().value)

        # VERSION
        version = self._parseVersion()

        # GRAPH 子句（固定位置，在 TIME/TOPK/LIMIT 之前）
        graph_clause = None
        graph_limit_val = None
        if self._match(TokenType.GRAPH):
            graph_clause = self._parseGraphClause()
            graph_limit_val = graph_clause.limit

        # 按书写顺序收集 TIME / TOPK / LIMIT
        ops: List[Tuple[str, Any]] = []
        time_filter = None
        limit = graph_limit_val  # GRAPH 自己的 LIMIT
        topk = None

        # 把 GRAPH 的 LIMIT 也放入 ops（顺序在所有 TIME/TOPK/LIMIT 之前）
        if graph_limit_val is not None:
            ops.append(("limit", graph_limit_val))

        # 循环解析 TIME / TOPK / LIMIT
        while True:
            consumed = False
            # TIME
            if self._match(TokenType.TIME):
                tf = self._parseTimeFilter()
                if tf is not None:
                    time_filter = tf
                    ops.append(("time", tf))
                    consumed = True
            # TOPK
            elif self._match(TokenType.TOPK):
                k = self._parseTopK()
                if k is not None:
                    topk = k
                    ops.append(("topk", k))
                    consumed = True
            # LIMIT
            elif self._match(TokenType.LIMIT):
                n = self._parseLimit()
                if n is not None:
                    limit = n
                    ops.append(("limit", n))
                    consumed = True
            if not consumed:
                break

        # WRAP 包装语法
        wrapped_sql = None
        if self._match(TokenType.WRAP):
            self._advance()
            self._expect(TokenType.LPAREN)
            # 收集括号内的完整 SQL 字符串，保留空格分隔
            paren_depth = 1
            parts = []
            while paren_depth > 0 and self.current.type != TokenType.EOF:
                if self.current.type == TokenType.LPAREN:
                    paren_depth += 1
                    parts.append(self._advance().value)
                elif self.current.type == TokenType.RPAREN:
                    paren_depth -= 1
                    if paren_depth > 0:
                        parts.append(self._advance().value)
                    else:
                        self._advance()  # 消耗最后的 )
                        break
                else:
                    # 非括号token，加空格分隔避免连成一词
                    token_val = self._advance().value
                    if parts and not parts[-1].endswith(" ") and not parts[-1].endswith("("):
                        parts.append(" ")
                    parts.append(token_val)
            if paren_depth != 0:
                raise ParseError("Unmatched parentheses in WRAP", self.current)
            wrapped_sql = "".join(parts).strip()

        return SelectStatement(
            fields=fields,
            conditions=conditions,
            version=version,
            ops=ops,
            time_filter=time_filter,
            limit=limit,
            topk=topk,
            search_slots=search_slots,
            search_topk=search_topk,
            graph_clause=graph_clause,
            wrapped_sql=wrapped_sql,
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

        # DRYRUN / DRY RUN 修饰符
        kwargs: Dict[str, Any] = {}
        if self._matchAdvance(TokenType.DRYRUN):
            kwargs["dry_run"] = True
        elif self._matchAdvance(TokenType.DRY):
            self._expect(TokenType.RUN)
            kwargs["dry_run"] = True

        return DeleteStatement(conditions=conditions, kwargs=kwargs)

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
        elif self._matchAdvance(TokenType.SNAPSHOT):
            # CREATE SNAPSHOT
            return SnapshotStatement(action="create")
        else:
            raise ParseError("Expected DATABASE, USER, or SNAPSHOT after CREATE", self.current)

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
        # LIST SNAPSHOTS
        elif self._matchAdvance(TokenType.SNAPSHOTS):
            return SnapshotStatement(action="list")
        else:
            raise ParseError("Expected DATABASES, USERS, or SNAPSHOTS after LIST", self.current)

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

    # =========================================================================
    # 函数/视图定义
    # =========================================================================

    def _parseDefine(self) -> DefineStatement:
        """解析 DEFINE 语句

        语法：DEFINE view_name AS SELECT ...
        注意：调用此方法时，DEFINE token 已被 parse() 中的 _matchAdvance 消耗
        """
        name = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.AS)

        # 收集剩余 token 直到分号（构建完整 SQL）
        parts = []
        while self.current.type not in (TokenType.EOF, TokenType.SEMICOLON):
            parts.append(self.current.value)
            self._advance()

        sql = " ".join(parts).strip()
        if self.current.type == TokenType.SEMICOLON:
            self._advance()

        return DefineStatement(name=name, sql=sql)


def parse(sql: str) -> ASTNode:
    """便捷函数：解析 SQL 语句"""
    tokens = tokenize(sql)
    parser = Parser(tokens)
    return parser.parse()
