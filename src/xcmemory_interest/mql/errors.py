"""
MQL - Memory Query Language 错误定义
"""


class MQLError(Exception):
    """MQL 基础错误"""
    pass


class LexerError(MQLError):
    """词法分析错误"""
    def __init__(self, message: str, line: int = 0, column: int = 0):
        self.line = line
        self.column = column
        super().__init__(f"Lexer Error at line {line}, col {column}: {message}")


class ParseError(MQLError):
    """语法解析错误"""
    def __init__(self, message: str, token=None):
        self.token = token
        super().__init__(f"Parse Error: {message}")


class ExecutionError(MQLError):
    """执行错误"""
    pass


class ValidationError(MQLError):
    """验证错误"""
    pass
