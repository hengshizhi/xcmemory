"""
MQL Lexer
"""

import re
from dataclasses import dataclass
from typing import List, Tuple
from enum import Enum, auto

from .errors import LexerError


class TokenType(Enum):
    SELECT = auto()
    INSERT = auto()
    UPDATE = auto()
    DELETE = auto()
    FROM = auto()
    WHERE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    INTO = auto()
    VALUES = auto()
    SET = auto()
    LIKE = auto()
    IN = auto()
    NULL = auto()
    TRUE = auto()
    FALSE = auto()
    IDENTIFIER = auto()
    STRING = auto()
    NUMBER = auto()
    MUL = auto()
    EQ = auto()
    NE = auto()
    LT = auto()
    GT = auto()
    LE = auto()
    GE = auto()
    ASSIGN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    DOT = auto()
    SEMICOLON = auto()
    WS = auto()
    COMMENT = auto()
    EOF = auto()
    SEARCH = auto()
    TOPK = auto()
    VERSION = auto()
    LIMIT = auto()
    # 系统管理
    CREATE = auto()
    DROP = auto()
    LIST = auto()
    USE = auto()
    DATABASE = auto()
    DATABASES = auto()
    SYSTEMS = auto()
    # 用户管理
    USER = auto()
    USERS = auto()
    GRANT = auto()
    REVOKE = auto()
    ON = auto()
    TO = auto()
    GENERATE = auto()
    KEY = auto()
    FOR = auto()
    # 权限类型
    READ = auto()
    WRITE = auto()
    ADMIN = auto()


KEYWORDS = {
    "select": TokenType.SELECT,
    "insert": TokenType.INSERT,
    "update": TokenType.UPDATE,
    "delete": TokenType.DELETE,
    "from": TokenType.FROM,
    "where": TokenType.WHERE,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
    "into": TokenType.INTO,
    "values": TokenType.VALUES,
    "set": TokenType.SET,
    "like": TokenType.LIKE,
    "in": TokenType.IN,
    "null": TokenType.NULL,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "search": TokenType.SEARCH,
    "topk": TokenType.TOPK,
    "version": TokenType.VERSION,
    "limit": TokenType.LIMIT,
    # 系统管理
    "create": TokenType.CREATE,
    "drop": TokenType.DROP,
    "list": TokenType.LIST,
    "use": TokenType.USE,
    "database": TokenType.DATABASE,
    "databases": TokenType.DATABASES,
    "systems": TokenType.SYSTEMS,
    # 用户管理
    "user": TokenType.USER,
    "users": TokenType.USERS,
    "grant": TokenType.GRANT,
    "revoke": TokenType.REVOKE,
    "on": TokenType.ON,
    "to": TokenType.TO,
    "generate": TokenType.GENERATE,
    "key": TokenType.KEY,
    "for": TokenType.FOR,
    # 权限类型
    "read": TokenType.READ,
    "write": TokenType.WRITE,
    "admin": TokenType.ADMIN,
}


OPERATORS = ["<=", ">=", "!=", "==", "=", "<", ">"]


@dataclass
class Token:
    type: TokenType
    value: str
    line: int = 0
    column: int = 0

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.column})"


class Lexer:
    PATTERNS = [
        (r"--.*", TokenType.COMMENT),
        (r"\s+", TokenType.WS),
        (r"'>'[^']*'<'", None),
        (r'"[^"]*"', TokenType.STRING),
        (r"'[^']*'", TokenType.STRING),
        (r"-?\d+\.?\d*", TokenType.NUMBER),
        (r"\[", TokenType.LBRACKET),
        (r"\]", TokenType.RBRACKET),
        (r"\(", TokenType.LPAREN),
        (r"\)", TokenType.RPAREN),
        (r",", TokenType.COMMA),
        (r"\.", TokenType.DOT),
        (r";", TokenType.SEMICOLON),
        (r":=", TokenType.ASSIGN),
        (r"\*", TokenType.MUL),
        (r"\w+", TokenType.IDENTIFIER),
    ]

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.text):
            match = self._advance_token()
            if match is not None:
                token_type, token_value = match
                if token_type == TokenType.IDENTIFIER:
                    token_type = KEYWORDS.get(token_value.lower(), TokenType.IDENTIFIER)
                elif token_type == TokenType.STRING:
                    if token_value in OPERATORS:
                        token_type = TokenType.EQ  # Simplified: all operators become EQ for now

                if token_type not in (TokenType.WS, TokenType.COMMENT):
                    self.tokens.append(Token(token_type, token_value, self.line, self.column - len(token_value)))

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return self.tokens

    def _advance_token(self) -> Tuple[TokenType, str]:
        # Handle query sentence <...>
        if self.pos < len(self.text) and self.text[self.pos] == "<":
            end = self.pos + 1
            bracket_count = 1
            while end < len(self.text) and bracket_count > 0:
                if self.text[end] == "<":
                    bracket_count += 1
                elif self.text[end] == ">":
                    bracket_count -= 1
                end += 1
            if bracket_count == 0:
                value = self.text[self.pos:end]
                self.pos = end
                return (TokenType.STRING, value)

        # Check for operators first
        for op in sorted(OPERATORS, key=len, reverse=True):
            if self.text.startswith(op, self.pos):
                self.pos += len(op)
                for _ in op:
                    self.column += 1
                return (TokenType.STRING, op)

        # Try patterns
        for pattern, token_type in self.PATTERNS:
            regex = re.compile(pattern)
            match = regex.match(self.text, self.pos)
            if match:
                value = match.group()
                self.pos = match.end()

                for ch in value:
                    if ch == "\n":
                        self.line += 1
                        self.column = 1
                    else:
                        self.column += 1

                if token_type is None:
                    return self._advance_token()

                return (token_type, value)

        raise LexerError(f"Unexpected character: {self.text[self.pos]!r}", self.line, self.column)


def tokenize(text: str) -> List[Token]:
    lexer = Lexer(text)
    return lexer.tokenize()