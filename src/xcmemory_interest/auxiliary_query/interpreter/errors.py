"""
Interpreter Errors
"""


class InterpreterError(Exception):
    """解释器异常基类"""
    pass


class BindingNotFoundError(InterpreterError):
    """绑定对象未找到"""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Binding '{name}' not found in context")


class MethodNotFoundError(InterpreterError):
    """方法不存在"""

    def __init__(self, obj_name: str, method_name: str):
        self.obj_name = obj_name
        self.method_name = method_name
        super().__init__(f"Method '{method_name}' not found on '{obj_name}'")


class ParseError(InterpreterError):
    """语法解析错误"""

    def __init__(self, message: str, expression: str = None):
        self.message = message
        self.expression = expression
        if expression:
            super().__init__(f"Parse error in '{expression}': {message}")
        else:
            super().__init__(f"Parse error: {message}")
