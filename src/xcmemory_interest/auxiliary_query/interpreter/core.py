"""
Interpreter - DSL 表达式解释器
"""

from typing import Any, Dict, List, Optional

from .errors import (
    InterpreterError,
    BindingNotFoundError,
    MethodNotFoundError,
    ParseError,
)
from .parser import parse_expression


class Interpreter:
    """
    解释器

    绑定运行时对象，通过 DSL 表达式调用方法。

    语法：
        <对象名>.<方法名>(<参数名>=<值>, ...)

    示例：
        inter = Interpreter()
        inter.bind("crud", crud_instance)
        result = inter.eval("crud.read(memory_id='mem_123')")
    """

    def __init__(self):
        """初始化解释器"""
        self._context: Dict[str, Any] = {}
        self._variables: Dict[str, Any] = {}

    # ---- 绑定管理 ----

    def bind(self, name: str, obj: Any) -> "Interpreter":
        """
        绑定对象到命名空间

        Args:
            name: 对象名（用于表达式中引用）
            obj: 对象实例

        Returns:
            self（支持链式调用）
        """
        self._context[name] = obj
        return self

    def unbind(self, name: str) -> bool:
        """
        解除绑定

        Args:
            name: 对象名

        Returns:
            是否成功解除
        """
        if name in self._context:
            del self._context[name]
            return True
        return False

    def bound_names(self) -> List[str]:
        """
        返回所有已绑定的名称

        Returns:
            绑定名称列表
        """
        return list(self._context.keys())

    def get_bound(self, name: str) -> Optional[Any]:
        """
        获取绑定的对象

        Args:
            name: 对象名

        Returns:
            绑定的对象，不存在返回 None
        """
        return self._context.get(name)

    # ---- 变量访问 ----

    def set_var(self, name: str, value: Any):
        """
        设置临时变量

        Args:
            name: 变量名
            value: 值
        """
        self._variables[name] = value

    def get_var(self, name: str) -> Any:
        """
        获取变量值

        Args:
            name: 变量名

        Returns:
            变量值

        Raises:
            KeyError: 变量不存在
        """
        if name not in self._variables:
            raise KeyError(f"Variable '{name}' not found")
        return self._variables[name]

    def clear_vars(self):
        """清空所有变量"""
        self._variables.clear()

    # ---- 表达式执行 ----

    def eval(self, expression: str) -> Any:
        """
        执行表达式

        Args:
            expression: DSL 表达式，如 "obj.method(arg=1)"

        Returns:
            方法调用的返回值

        Raises:
            InterpreterError: 表达式解析或执行错误
            BindingNotFoundError: 绑定对象未找到
            MethodNotFoundError: 方法不存在
            ParseError: 语法解析错误
        """
        # 解析表达式
        try:
            obj_name, method_name, args = parse_expression(expression)
        except ParseError as e:
            raise InterpreterError(f"Failed to parse expression: {e}") from e

        # 查找绑定的对象
        if obj_name not in self._context:
            raise BindingNotFoundError(obj_name)

        obj = self._context[obj_name]

        # 查找方法
        if not hasattr(obj, method_name):
            raise MethodNotFoundError(obj_name, method_name)

        method = getattr(obj, method_name)

        # 调用方法
        try:
            return method(**args)
        except TypeError as e:
            raise InterpreterError(
                f"Failed to call {obj_name}.{method_name}({args}): {e}"
            ) from e

    def execute(self, statements: str) -> List[Any]:
        """
        执行多条语句（换行分隔）

        Args:
            statements: 多行语句

        Returns:
            各语句的返回值列表
        """
        results = []
        for line in statements.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result = self.eval(line)
            results.append(result)
        return results

    # ---- 调试辅助 ----

    def inspect(self, name: str) -> Dict[str, Any]:
        """
        检查绑定对象的信息

        Args:
            name: 对象名

        Returns:
            包含方法列表等信息的字典
        """
        if name not in self._context:
            raise BindingNotFoundError(name)

        obj = self._context[name]
        methods = [m for m in dir(obj) if not m.startswith("_")]

        return {
            "name": name,
            "type": type(obj).__name__,
            "methods": methods,
            "num_methods": len(methods),
        }

    def help(self, name: str, method_name: str = None) -> str:
        """
        获取绑定对象的帮助信息

        Args:
            name: 对象名
            method_name: 方法名（可选）

        Returns:
            帮助文本
        """
        if name not in self._context:
            raise BindingNotFoundError(name)

        obj = self._context[name]

        if method_name:
            if not hasattr(obj, method_name):
                raise MethodNotFoundError(name, method_name)
            method = getattr(obj, method_name)
            doc = method.__doc__ or "No documentation"
            return f"{name}.{method_name}:\n\n{doc}"
        else:
            # 返回所有方法的帮助
            lines = [f"{name} ({type(obj).__name__}):\n"]
            for m in dir(obj):
                if m.startswith("_"):
                    continue
                method = getattr(obj, m)
                doc = (method.__doc__ or "No documentation").split("\n")[0]
                lines.append(f"  {m}: {doc}")
            return "\n".join(lines)

    # ---- 上下文管理 ----

    def __repr__(self) -> str:
        return f"Interpreter(bound={list(self._context.keys())})"
