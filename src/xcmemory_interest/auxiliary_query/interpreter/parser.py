"""
DSL 语法解析器
"""

import ast
import re
from typing import Any, Dict, List, Tuple

from .errors import ParseError


class ExpressionParser:
    """
    DSL 表达式解析器

    语法：
        <对象名>.<方法名>(<参数名>=<值>, ...)

    示例：
        crud.read(memory_id='mem_123')
        kv.get(key='cache:1', default=None)
        sql.select(table_name='memories', limit=10)
    """

    # 支持的字面量类型
    LITERAL_PATTERN = re.compile(
        r"""
        (?P<string>'[^']*'|"[^"]*")|    # 字符串字面量
        (?P<number>-?\d+\.?\d*)|        # 数字 (int/float)
        (?P<bool>true|false)|           # 布尔值
        (?P<none>null)|                  # None 值
        (?P<ident>\w+)|                 # 标识符
        (?P<op>[()=,.])|                # 操作符
        (?P<ws>\s+)|                    # 空白
        (?P<other>.+)                   # 其他
        """,
        re.VERBOSE
    )

    def __init__(self):
        self.tokens: List[Tuple[str, str]] = []
        self.pos: int = 0

    def tokenize(self, expression: str) -> List[Tuple[str, str]]:
        """将表达式分解为 token 列表"""
        tokens = []
        for match in self.LITERAL_PATTERN.finditer(expression):
            kind = match.lastgroup
            value = match.group()
            if kind == "ws":
                continue  # 跳过空白
            if kind == "other":
                raise ParseError(f"Unexpected character: {value}", expression)
            tokens.append((kind, value))
        return tokens

    def parse(self, expression: str) -> Tuple[str, str, Dict[str, Any]]:
        """
        解析表达式

        Returns:
            (object_name, method_name, arguments_dict)

        Raises:
            ParseError: 解析失败
        """
        self.tokens = self.tokenize(expression)
        self.pos = 0

        # 解析 object.method
        if len(self.tokens) < 3:
            raise ParseError("Expression too short", expression)

        # 第一个 token 应该是标识符（对象名）
        obj_name = self._expect("ident")

        # 点号
        self._expect("op", ".")

        # 方法名
        method_name = self._expect("ident")

        # 左括号
        self._expect("op", "(")

        # 解析参数
        args = {}
        if self._check("op", ")"):
            self.pos += 1
        else:
            while True:
                # 参数名
                arg_name = self._expect("ident")
                # 等号
                self._expect("op", "=")
                # 参数值
                arg_value = self._parse_value()
                args[arg_name] = arg_value

                if self._check("op", ")"):
                    self.pos += 1
                    break
                elif self._check("op", ","):
                    self.pos += 1
                    continue
                else:
                    raise ParseError(
                        f"Expected ')' or ',', got {self.tokens[self.pos]}",
                        expression
                    )

        # 检查是否还有多余 token
        if self.pos < len(self.tokens):
            raise ParseError(
                f"Unexpected token after expression: {self.tokens[self.pos]}",
                expression
            )

        return obj_name, method_name, args

    def _current(self) -> Tuple[str, str]:
        """返回当前 token"""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return ("", "")

    def _check(self, kind: str, value: str = None) -> bool:
        """检查当前 token 是否匹配"""
        k, v = self._current()
        if value is None:
            return k == kind
        return k == kind and v == value

    def _expect(self, kind: str, value: str = None) -> str:
        """期望某个 token，否则抛出异常"""
        k, v = self._current()
        if k != kind or (value is not None and v != value):
            raise ParseError(
                f"Expected {kind}{'='+value if value else ''}, got ({k}, {v})",
                None
            )
        self.pos += 1
        return v

    def _parse_value(self) -> Any:
        """解析值（字符串、数字、布尔、None）"""
        kind, value = self._current()

        if kind == "string":
            self.pos += 1
            # 去掉引号
            return value[1:-1]

        elif kind == "number":
            self.pos += 1
            if "." in value:
                return float(value)
            return int(value)

        elif kind == "bool":
            self.pos += 1
            return value == "true"

        elif kind == "none":
            self.pos += 1
            return None

        elif kind == "ident":
            # 可能是变量引用（暂不支持，视为错误）
            self.pos += 1
            raise ParseError(
                f"Unexpected identifier '{value}' as value (variables not supported yet)",
                None
            )

        else:
            raise ParseError(f"Unexpected token: ({kind}, {value})", None)


# 全局解析器实例
_parser = ExpressionParser()


def parse_expression(expression: str) -> Tuple[str, str, Dict[str, Any]]:
    """
    解析 DSL 表达式

    Args:
        expression: DSL 表达式

    Returns:
        (object_name, method_name, arguments_dict)
    """
    return _parser.parse(expression)
