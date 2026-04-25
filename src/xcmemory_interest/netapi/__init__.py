"""
API Server - HTTP + WebSocket API 层

提供 HTTP 和 WebSocket 接口访问记忆系统，支持 APIKey 认证。

使用方法：
    from xcmemory_interest.netapi import APIServer

    # 同时启动 HTTP 和 WebSocket
    server = APIServer(
        database_root="./data",
        host="0.0.0.0",
        port=8080,
        ws_port=8081,
    )
    server.start()

    # 或仅 HTTP
    server = APIServer(database_root="./data", port=8080)
    server.start()
"""

import asyncio
import json
import re
import threading
import http.server
import socketserver
import struct
import hashlib
import secrets
from pathlib import Path
from typing import Any, Dict, Optional, Callable, List
from dataclasses import dataclass, field
from enum import Enum

# WebSocket 支持
try:
    import websockets
    from websockets.server import serve, WebSocketServerProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

from ..user_manager import UserManager, AuthContext, AuthResult, PermissionType
from ..mql import Interpreter, QueryResult


# ============================================================================
# 常量
# ============================================================================

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_WEBSOCKET_FRAME_SIZE = 65536


# ============================================================================
# API 错误
# ============================================================================

class APIError(Exception):
    """API 错误"""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthRequiredError(APIError):
    """需要认证"""
    def __init__(self):
        super().__init__("Authentication required. Provide X-API-Key header.", status_code=401)


class ForbiddenError(APIError):
    """权限不足"""
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, status_code=403)


# ============================================================================
# HTTP 请求/响应
# ============================================================================

@dataclass
class HTTPRequest:
    """HTTP 请求"""
    method: str
    path: str
    headers: Dict[str, str]
    body: Optional[str] = None
    query_params: Dict[str, str] = None

    def get_json(self) -> Dict:
        """获取 JSON body"""
        if not self.body:
            return {}
        try:
            return json.loads(self.body)
        except json.JSONDecodeError:
            return {}


@dataclass
class HTTPResponse:
    """HTTP 响应"""
    status_code: int = 200
    headers: Dict[str, str] = None
    body: str = ""

    def to_bytes(self) -> bytes:
        if self.headers is None:
            self.headers = {}
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"
        if "Access-Control-Allow-Origin" not in self.headers:
            self.headers["Access-Control-Allow-Origin"] = "*"

        body_bytes = self.body.encode("utf-8")
        response_lines = [
            f"HTTP/1.1 {self.status_code} {self.status_text()}\r\n"
        ]
        for k, v in self.headers.items():
            response_lines.append(f"{k}: {v}\r\n")
        response_lines.append(f"Content-Length: {len(body_bytes)}\r\n")
        response_lines.append("\r\n")
        return "".join(response_lines).encode("utf-8") + body_bytes

    def status_text(self) -> str:
        status_map = {
            200: "OK",
            201: "Created",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            500: "Internal Server Error",
        }
        return status_map.get(self.status_code, "Unknown")


# ============================================================================
# WebSocket 消息
# ============================================================================

class WSMessageType(Enum):
    """WebSocket 消息类型"""
    TEXT = 1
    BINARY = 2
    PING = 9
    PONG = 10
    CLOSE = 8


@dataclass
class WSMessage:
    """WebSocket 消息"""
    type: WSMessageType
    data: Any = None

    @classmethod
    def text(cls, data: str) -> "WSMessage":
        return cls(type=WSMessageType.TEXT, data=data)

    @classmethod
    def json(cls, data: Dict) -> "WSMessage":
        return cls(type=WSMessageType.TEXT, data=json.dumps(data))

    @classmethod
    def ping(cls) -> "WSMessage":
        return cls(type=WSMessageType.PING)

    @classmethod
    def close(cls, code: int = 1000) -> "WSMessage":
        return cls(type=WSMessageType.CLOSE, data=code)


@dataclass
class WSClient:
    """WebSocket 客户端"""
    id: str
    username: Optional[str] = None
    auth_context: Optional[AuthContext] = None
    active_system: Optional[str] = None
    is_admin: bool = False


# ============================================================================
# API Server
# ============================================================================

class APIServer:
    """
    记忆系统 API Server

    提供 RESTful HTTP API 和 WebSocket API，支持 APIKey 认证。

    HTTP 端点：
    - POST /api/v1/query - 执行 MQL 语句
    - GET  /api/v1/systems - 列出记忆系统
    - POST /api/v1/systems - 创建记忆系统
    - GET  /api/v1/systems/<name> - 获取系统信息
    - DELETE /api/v1/systems/<name> - 删除记忆系统
    - POST /api/v1/systems/<name>/use - 设置当前系统
    - GET  /api/v1/users - 列出用户（仅管理员）
    - POST /api/v1/users - 创建用户（仅管理员）
    - DELETE /api/v1/users/<username> - 删除用户（仅管理员）
    - POST /api/v1/permissions - 授予权限（仅管理员）
    - DELETE /api/v1/permissions - 撤销权限（仅管理员）

    WebSocket 端点：
    - ws://host:port/ws - WebSocket 连接（需先认证）

    WebSocket 消息格式：
    - 发送认证: {"type": "auth", "api_key": "xi-user-xxx"}
    - 发送查询: {"type": "query", "sql": "SELECT * FROM memories"}
    - 发送切换系统: {"type": "use", "system": "system_name"}
    - 接收响应: {"type": "result", "data": {...}}
    """

    def __init__(
        self,
        database_root: str,
        host: str = "0.0.0.0",
        port: int = 8080,
        ws_port: Optional[int] = None,
        debug: bool = False,
        openai_config: Optional[Dict[str, str]] = None,
    ):
        self.database_root = Path(database_root)
        self.database_root.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.port = port
        self.ws_port = ws_port or (port + 1)  # 默认 WebSocket 端口 = HTTP 端口 + 1
        self.debug = debug
        self.openai_config = openai_config or {}

        # 用户管理器
        self.user_manager = UserManager(str(self.database_root))

        # PyAPI（延迟导入）
        self._pyapi = None

        # WebSocket 客户端
        self._ws_clients: Dict[str, WSClient] = {}
        self._ws_clients_lock = threading.Lock()

        # 路由表
        self._routes: Dict[str, Callable] = {}
        self._register_routes()

        # 服务器线程
        self._http_server_thread: Optional[threading.Thread] = None
        self._ws_server_future: Optional[asyncio.Future] = None

    @property
    def pyapi(self):
        """延迟加载 PyAPI（torch 不可用时优雅降级）"""
        if self._pyapi is None:
            try:
                from ..pyapi.core import PyAPI
                self._pyapi = PyAPI(str(self.database_root))
            except OSError as e:
                if "torch" in str(e) or "_C" in str(e) or "DLL" in str(e):
                    self._pyapi = None
                    return None
                raise
        return self._pyapi

    def _register_routes(self):
        """注册路由"""
        # MQL 查询
        self._routes["POST:/api/v1/query"] = self._handle_query
        self._routes["GET:/api/v1/query"] = self._handle_query_get

        # 系统管理
        self._routes["GET:/api/v1/systems"] = self._handle_list_systems
        self._routes["POST:/api/v1/systems"] = self._handle_create_system
        self._routes["GET:/api/v1/systems/(?P<name>[^/]+)"] = self._handle_get_system
        self._routes["PUT:/api/v1/systems/(?P<name>[^/]+)/holder"] = self._handle_set_system_holder
        self._routes["DELETE:/api/v1/systems/(?P<name>[^/]+)"] = self._handle_delete_system
        self._routes["POST:/api/v1/systems/(?P<name>[^/]+)/use"] = self._handle_use_system

        # 用户管理
        self._routes["GET:/api/v1/users"] = self._handle_list_users
        self._routes["POST:/api/v1/users"] = self._handle_create_user
        self._routes["GET:/api/v1/users/(?P<username>[^/]+)"] = self._handle_get_user
        self._routes["DELETE:/api/v1/users/(?P<username>[^/]+)"] = self._handle_delete_user
        self._routes["POST:/api/v1/users/(?P<username>[^/]+)/generate_key"] = self._handle_generate_key

        # 权限管理
        self._routes["POST:/api/v1/permissions"] = self._handle_grant_permission
        self._routes["DELETE:/api/v1/permissions"] = self._handle_revoke_permission

        # LLM 查询
        self._routes["POST:/api/v1/nl-query"] = self._handle_nl_query

        # LLM 权限管理
        self._routes["POST:/api/v1/users/(?P<username>[^/]+)/llm-toggle"] = self._handle_llm_toggle

        # 健康检查
        self._routes["GET:/health"] = self._handle_health

        # WebSocket 升级
        self._routes["GET:/ws"] = self._handle_ws_upgrade

    # =========================================================================
    # 认证
    # =========================================================================

    def _authenticate(self, request: HTTPRequest) -> AuthContext:
        """认证请求"""
        import sys
        # 尝试多种可能的头部名称变体
        header_candidates = [
            "X-Api-Key",      # 实际观察到的 header（单数 Api）
            "x-api-key",      # 全小写（规范化后）
            "X-API-Key",      # 标准格式
            "x-apikey",       # 无连字符
            "X-Apikey",       # 无连字符大写
        ]
        
        api_key = ""
        for candidate in header_candidates:
            api_key = request.headers.get(candidate, "")
            if api_key:
                if self.debug:
                    sys.stderr.write(
                        f"[netapi DEBUG] found header '{candidate}' with key: {api_key[:20]}...\n"
                    )
                    sys.stderr.flush()
                break

        if self.debug and not api_key:
            sys.stderr.write(
                f"[netapi DEBUG] auth headers: {list(request.headers.keys())} | "
                f"no API key found in any candidate header\n"
            )
            sys.stderr.flush()

        if not api_key:
            if request.query_params:
                api_key = request.query_params.get("api_key", "")

        if not api_key:
            raise AuthRequiredError()

        result = self.user_manager.authenticate(api_key)
        if self.debug:
            sys.stderr.write(f"[netapi DEBUG] auth result: {result}\n")
            sys.stderr.flush()
        if not result.success:
            raise APIError(result.error or "Authentication failed", status_code=401)

        return AuthContext.from_auth_result(result)

    def _authenticate_api_key(self, api_key: str) -> AuthContext:
        """认证 APIKey（用于 WebSocket）"""
        if not api_key:
            raise AuthRequiredError()

        result = self.user_manager.authenticate(api_key)
        if not result.success:
            raise APIError(result.error or "Authentication failed", status_code=401)

        return AuthContext.from_auth_result(result)

    def _require_admin(self, auth: AuthContext):
        """要求管理员权限"""
        if not auth.is_superadmin:
            raise ForbiddenError("Admin permission required")

    # =========================================================================
    # 路由处理
    # =========================================================================

    def _route(self, method: str, path: str) -> tuple:
        """路由匹配"""
        key = f"{method}:{path}"
        if key in self._routes:
            return key, {}

        for pattern, handler in self._routes.items():
            if ":" not in pattern:
                continue
            pat_method, pat_path = pattern.split(":", 1)
            if pat_method != method:
                continue
            match = re.match(f"^{pat_path}$", path)
            if match:
                return pattern, match.groupdict()

        return None, {}

    def handle_request(self, request: HTTPRequest) -> HTTPResponse:
        """处理请求"""
        try:
            # 健康检查不需要认证
            if request.path == "/health":
                return self._handle_health(request)

            # WebSocket 升级
            if request.path == "/ws":
                return self._handle_ws_upgrade(request, None, {})

            auth = self._authenticate(request)

            key, params = self._route(request.method, request.path)
            if key is None:
                return HTTPResponse(
                    status_code=404,
                    body=json.dumps({"error": f"Not found: {request.method} {request.path}"}),
                )

            handler = self._routes[key]
            return handler(request, auth, params)

        except APIError as e:
            return HTTPResponse(
                status_code=e.status_code,
                body=json.dumps({"error": e.message}),
            )
        except Exception as e:
            if self.debug:
                import sys, traceback
                sys.stderr.write(f"[netapi DEBUG] Unhandled exception: {e}\n")
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
            return HTTPResponse(
                status_code=500,
                body=json.dumps({"error": str(e)}),
            )

    # =========================================================================
    # 处理器
    # =========================================================================

    def _handle_query(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """执行 MQL 查询"""
        data = request.get_json()
        mql = data.get("mql", "") or data.get("sql", "")  # 兼容 sql
        script = data.get("script", "")  # 多行 MQL（分号分隔）

        if not mql and not script:
            raise APIError("mql or sql field is required")

        interpreter = Interpreter()
        interpreter.bind("api", self.pyapi)
        interpreter.bind("um", self.user_manager)
        interpreter.set_auth_context(auth)

        active = self.pyapi.active_system
        if active:
            interpreter.bind("mem", active)

        # 多行 MQL 执行
        if script:
            results = interpreter.execute_script(script)

            all_data = []
            all_affected = 0
            all_memory_ids = []
            all_messages = []

            for r in results:
                all_data.extend(r.data if r.data else [])
                all_affected += r.affected_rows or 0
                all_memory_ids.extend(r.memory_ids or [] if r.memory_ids else [])
                if r.message:
                    all_messages.append(r.message)

            return HTTPResponse(
                status_code=200,
                body=json.dumps({
                    "type": "script",
                    "data": all_data,
                    "affected_rows": all_affected,
                    "memory_ids": all_memory_ids,
                    "message": "; ".join(all_messages) if all_messages else "OK",
                    "script_results": [
                        {"type": r.type, "data": r.data, "affected": r.affected_rows, "message": r.message}
                        for r in results
                    ],
                }),
            )
        else:
            result = interpreter.execute(mql)

            return HTTPResponse(
                status_code=200,
                body=json.dumps({
                    "type": result.type,
                    "data": result.data,
                    "affected_rows": result.affected_rows,
                    "memory_ids": result.memory_ids,
                    "message": result.message,
                }),
            )

    def _handle_query_get(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """GET 方式执行查询"""
        sql = request.query_params.get("sql", "") if request.query_params else ""
        return self._handle_query(
            HTTPRequest(method="POST", path="/api/v1/query", headers=request.headers, body=json.dumps({"sql": sql})),
            auth, params
        )

    def _handle_list_systems(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """列出记忆系统"""
        systems = self.pyapi.list_all_systems()

        if not auth.is_superadmin:
            allowed = set(auth.permissions.keys())
            if "*" in allowed:
                allowed = set(s["name"] for s in systems)
            systems = [s for s in systems if s["name"] in allowed]

        return HTTPResponse(body=json.dumps({"systems": systems}))

    def _handle_create_system(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """创建记忆系统"""
        self._require_admin(auth)

        data = request.get_json()
        name = data.get("name")
        enable_interest = data.get("enable_interest_mode", False)

        if not name:
            raise APIError("name field is required")

        system = self.pyapi.create_system(name, enable_interest_mode=enable_interest)
        return HTTPResponse(status_code=201, body=json.dumps({"name": name, "message": f"Created system '{name}'"}))

    def _handle_get_system(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """获取系统信息"""
        name = params["name"]

        if not auth.is_superadmin and name not in auth.permissions:
            raise ForbiddenError(f"No permission to access system '{name}'")

        system = self.pyapi.get_system(name)
        if not system:
            raise APIError(f"System '{name}' not found", status_code=404)

        stats = system.get_stats()
        return HTTPResponse(body=json.dumps(stats))

    def _handle_set_system_holder(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """设置记忆系统持有者"""
        name = params["name"]

        if not auth.is_superadmin and name not in auth.permissions:
            raise ForbiddenError(f"No permission to access system '{name}'")

        body = request.get_json()
        holder = body.get("holder", "")
        if not holder:
            raise APIError("holder field is required", status_code=400)

        ok = self.pyapi.set_system_holder(name, holder)
        if not ok:
            raise APIError(f"System '{name}' not found", status_code=404)

        return HTTPResponse(body=json.dumps({"message": f"Holder set to '{holder}' for system '{name}'"}))

    def _handle_delete_system(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """删除记忆系统"""
        self._require_admin(auth)

        name = params["name"]
        ok = self.pyapi.delete_system(name)

        if not ok:
            raise APIError(f"System '{name}' not found", status_code=404)

        return HTTPResponse(body=json.dumps({"message": f"Deleted system '{name}'"}))

    def _handle_use_system(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """设置当前系统"""
        name = params["name"]

        if not auth.is_superadmin and name not in auth.permissions:
            raise ForbiddenError(f"No permission to access system '{name}'")

        self.pyapi.set_active_system(name)
        return HTTPResponse(body=json.dumps({"message": f"Switched to system '{name}'", "active_system": name}))

    def _handle_list_users(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """列出用户"""
        self._require_admin(auth)
        users = self.user_manager.list_users()
        return HTTPResponse(body=json.dumps({"users": users}))

    def _handle_create_user(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """创建用户"""
        self._require_admin(auth)

        data = request.get_json()
        username = data.get("username")

        if not username:
            raise APIError("username field is required")

        ok, result = self.user_manager.create_user(username)
        if not ok:
            raise APIError(result)

        return HTTPResponse(status_code=201, body=json.dumps({"username": username, "api_key": result}))

    def _handle_get_user(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """获取用户信息"""
        self._require_admin(auth)

        username = params["username"]
        user = self.user_manager.get_user(username)

        if not user:
            raise APIError(f"User '{username}' not found", status_code=404)

        return HTTPResponse(body=json.dumps(user))

    def _handle_delete_user(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """删除用户"""
        self._require_admin(auth)

        username = params["username"]
        ok, msg = self.user_manager.delete_user(username)

        if not ok:
            raise APIError(msg)

        return HTTPResponse(body=json.dumps({"message": msg}))

    def _handle_generate_key(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """生成新 APIKey"""
        username = params["username"]

        if not auth.is_superadmin and auth.username != username:
            raise ForbiddenError("Can only generate your own key")

        new_key = self.user_manager.generate_api_key(username)
        return HTTPResponse(body=json.dumps({"username": username, "api_key": new_key}))

    def _handle_grant_permission(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """授予权限"""
        self._require_admin(auth)

        data = request.get_json()
        username = data.get("username")
        system_name = data.get("system")
        permission = data.get("permission")

        if not username or not system_name or not permission:
            raise APIError("username, system, permission fields are required")

        ok, msg = self.user_manager.grant_permission(
            username=username,
            system_name=system_name,
            permission=PermissionType(permission),
            granted_by=auth.username,
        )

        if not ok:
            raise APIError(msg)

        return HTTPResponse(body=json.dumps({"message": msg}))

    def _handle_revoke_permission(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """撤销权限"""
        self._require_admin(auth)

        data = request.get_json()
        username = data.get("username")
        system_name = data.get("system")
        permission = data.get("permission")

        if not username or not system_name or not permission:
            raise APIError("username, system, permission fields are required")

        ok, msg = self.user_manager.revoke_permission(
            username=username,
            system_name=system_name,
            permission=PermissionType(permission),
        )

        if not ok:
            raise APIError(msg)

        return HTTPResponse(body=json.dumps({"message": msg}))

    def _serialize_memory(self, item):
        """将 Memory 对象（或 dict）转为 JSON 可序列化的 dict"""
        if isinstance(item, dict):
            d = {}
            for k, v in item.items():
                d[k] = self._serialize_memory(v)
            return d
        # 优先使用 to_dict()（Memory/搜索结果等）
        if hasattr(item, "to_dict"):
            return self._serialize_memory(item.to_dict())
        if hasattr(item, "__dict__"):
            d = {}
            for k, v in vars(item).items():
                if k.startswith("_"):
                    continue
                # numpy 数组
                if hasattr(v, "tolist"):
                    d[k] = v.tolist()
                # datetime
                elif hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                # 递归处理
                elif isinstance(v, dict):
                    d[k] = self._serialize_memory(v)
                elif isinstance(v, list):
                    d[k] = [self._serialize_memory(x) for x in v]
                elif hasattr(v, "__dict__"):
                    d[k] = self._serialize_memory(v)
                else:
                    try:
                        json.dumps(v)
                        d[k] = v
                    except (TypeError, ValueError):
                        d[k] = str(v)
            return d
        return item

    def _handle_nl_query(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """自然语言查询"""
        import asyncio

        # 检查 LLM 权限
        if not auth.llm_enabled:
            raise ForbiddenError(
                "LLM query permission required. Ask admin to enable it via "
                "POST /api/v1/users/<username>/llm-toggle"
            )

        data = request.get_json()
        nl_query = data.get("query", "")
        top_k = int(data.get("top_k", 10))

        if not nl_query:
            raise APIError("query field is required")

        active = self.pyapi.active_system if self.pyapi else None
        if not active:
            raise APIError("No active memory system. Use POST /api/v1/systems/<name>/use first.")

        if not self.openai_config.get("api_key"):
            raise APIError("LLM not configured on server")

        # 构建 LLM 客户端
        from openai import AsyncOpenAI
        llm_client = AsyncOpenAI(
            api_key=self.openai_config["api_key"],
            base_url=self.openai_config.get("base_url", "https://openrouter.ai/api/v1"),
        )
        model = self.openai_config.get("model", "xiaomi/mimo-v2-flash")

        # 导入延迟避免循环依赖
        try:
            from ..nl.pipeline import NLSearchPipeline
        except ImportError as import_err:
            if "torch" in str(import_err) or "_C" in str(import_err) or "DLL" in str(import_err):
                raise APIError(
                    "torch is not available in this environment. "
                    "NL query requires torch for vector operations."
                )
            raise

        async def _run():
            pipeline = NLSearchPipeline(
                llm_client=llm_client,
                memory_system=active,
                model=model,
                debug=self.debug,
            )
            return await pipeline.run(nl_query=nl_query, history=[], top_k=top_k)

        result = asyncio.run(_run())

        # 诊断：找出哪个字段无法序列化
        def _find_unserializable(obj, path=""):
            if isinstance(obj, (str, int, float, bool, type(None))):
                return None
            if isinstance(obj, list):
                for i, item in enumerate(obj):
                    f = _find_unserializable(item, f"{path}[{i}]")
                    if f:
                        return f
                return None
            if isinstance(obj, dict):
                for k, v in obj.items():
                    f = _find_unserializable(v, f"{path}.{k}")
                    if f:
                        return f
                return None
            # 不在上面的基本类型中 → 无法序列化
            return f"{path}: {type(obj).__name__}"

        resp_data = {
            "type": result.get("type"),
            "query": nl_query,
            "response": result.get("response", ""),
            "mql": result.get("mql", ""),
            "slots": result.get("slots", {}),
            "result_count": len(result.get("result", [])),
            "results": [self._serialize_memory(r) for r in result.get("result", [])],
            "intent": result.get("intent", {}),
            "writes": len(result.get("writes", [])),
            "llm_calls": result.get("llm_calls", 0),
        }

        try:
            body = json.dumps(resp_data)
        except (TypeError, ValueError) as serial_err:
            bad_field = _find_unserializable(resp_data)
            raise APIError(f"JSON serialize failed at {bad_field}: {serial_err}")

        return HTTPResponse(body=body)

    def _handle_llm_toggle(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """开启/关闭用户的 LLM 权限"""
        self._require_admin(auth)

        username = params["username"]
        data = request.get_json()
        enabled = bool(data.get("enable", True))

        ok, msg = self.user_manager.set_llm_permission(username, enabled)
        if not ok:
            raise APIError(msg)

        return HTTPResponse(body=json.dumps({"message": msg, "username": username, "llm_enabled": enabled}))

    def _handle_health(self, request: HTTPRequest, auth: AuthContext = None, params: Dict = None) -> HTTPResponse:
        """健康检查"""
        return HTTPResponse(body=json.dumps({
            "status": "ok",
            "version": "0.2.0",
            "http_port": self.port,
            "ws_port": self.ws_port if WEBSOCKETS_AVAILABLE else None,
        }))

    def _handle_ws_upgrade(self, request: HTTPRequest, auth: AuthContext, params: Dict) -> HTTPResponse:
        """WebSocket 升级请求（返回指引）"""
        return HTTPResponse(
            status_code=426,
            body=json.dumps({
                "error": "Use WebSocket protocol to connect to /ws endpoint",
                "port": self.ws_port,
                "instructions": {
                    "connect": f"ws://{self.host}:{self.ws_port}/ws",
                    "auth": '{"type": "auth", "api_key": "xi-user-xxx"}',
                    "query": '{"type": "query", "sql": "SELECT * FROM memories"}',
                }
            })
        )

    # =========================================================================
    # WebSocket 处理
    # =========================================================================

    async def _ws_handler(self, websocket, path: str):
        """WebSocket 处理器"""
        client_id = secrets.token_hex(8)
        client = WSClient(id=client_id)
        authenticated = False

        with self._ws_clients_lock:
            self._ws_clients[client_id] = client

        try:
            async for message in websocket:
                try:
                    # 解析消息
                    if isinstance(message, bytes):
                        data = message.decode("utf-8")
                    else:
                        data = message

                    try:
                        msg_json = json.loads(data)
                    except json.JSONDecodeError:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "error": "Invalid JSON"
                        }))
                        continue

                    msg_type = msg_json.get("type", "")

                    # 认证
                    if msg_type == "auth":
                        api_key = msg_json.get("api_key", "")
                        try:
                            auth_ctx = self._authenticate_api_key(api_key)
                            client.auth_context = auth_ctx
                            client.username = auth_ctx.username
                            client.is_admin = auth_ctx.is_superadmin
                            authenticated = True

                            await websocket.send(json.dumps({
                                "type": "auth_result",
                                "success": True,
                                "username": auth_ctx.username,
                                "is_admin": auth_ctx.is_superadmin,
                            }))
                        except Exception as e:
                            await websocket.send(json.dumps({
                                "type": "auth_result",
                                "success": False,
                                "error": str(e),
                            }))

                    elif not authenticated:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "error": "Not authenticated. Send auth message first.",
                        }))

                    # 查询
                    elif msg_type == "query" or msg_type == "mql":
                        if not client.auth_context:
                            await websocket.send(json.dumps({
                                "type": "error",
                                "error": "Not authenticated",
                            }))
                            continue

                        # 支持 mql 或 script 字段
                        mql = msg_json.get("mql", "") or msg_json.get("sql", "")
                        script = msg_json.get("script", "")  # 多行 MQL（分号分隔）

                        if not mql and not script:
                            await websocket.send(json.dumps({
                                "type": "result",
                                "success": False,
                                "error": "mql or script field is required",
                            }))
                            continue

                        interpreter = Interpreter()
                        interpreter.bind("api", self.pyapi)
                        interpreter.bind("um", self.user_manager)
                        interpreter.set_auth_context(client.auth_context)

                        active = self.pyapi.active_system
                        if active:
                            interpreter.bind("mem", active)

                        # 多行 MQL 执行
                        if script:
                            # 执行多行脚本（分号分隔）
                            results = interpreter.execute_script(script)

                            # 收集所有结果
                            all_data = []
                            all_affected = 0
                            all_memory_ids = []
                            all_messages = []

                            for r in results:
                                all_data.extend(r.data if r.data else [])
                                all_affected += r.affected_rows or 0
                                all_memory_ids.extend(r.memory_ids or [] if r.memory_ids else [])
                                if r.message:
                                    all_messages.append(r.message)

                            await websocket.send(json.dumps({
                                "type": "result",
                                "success": True,
                                "data": all_data,
                                "affected_rows": all_affected,
                                "memory_ids": all_memory_ids,
                                "message": "; ".join(all_messages) if all_messages else "OK",
                                "script_results": [
                                    {"type": r.type, "data": r.data, "affected": r.affected_rows, "message": r.message}
                                    for r in results
                                ],
                            }))
                        else:
                            # 单条 MQL
                            result = interpreter.execute(mql)

                            await websocket.send(json.dumps({
                                "type": "result",
                                "success": True,
                                "data": result.data,
                                "affected_rows": result.affected_rows,
                                "memory_ids": result.memory_ids,
                                "message": result.message,
                            }))

                    # 切换系统
                    elif msg_type == "use":
                        if not client.auth_context:
                            await websocket.send(json.dumps({
                                "type": "error",
                                "error": "Not authenticated",
                            }))
                            continue

                        system_name = msg_json.get("system", "")
                        if not system_name:
                            await websocket.send(json.dumps({
                                "type": "result",
                                "success": False,
                                "error": "system field is required",
                            }))
                            continue

                        # 检查权限
                        if not client.is_admin and system_name not in client.auth_context.permissions:
                            await websocket.send(json.dumps({
                                "type": "result",
                                "success": False,
                                "error": f"No permission to access system '{system_name}'",
                            }))
                            continue

                        self.pyapi.set_active_system(system_name)
                        client.active_system = system_name

                        await websocket.send(json.dumps({
                            "type": "result",
                            "success": True,
                            "message": f"Switched to system '{system_name}'",
                            "active_system": system_name,
                        }))

                    # 心跳
                    elif msg_type == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))

                    else:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "error": f"Unknown message type: {msg_type}",
                        }))

                except Exception as e:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "error": str(e),
                    }))
        finally:
            with self._ws_clients_lock:
                if client_id in self._ws_clients:
                    del self._ws_clients[client_id]

    async def _ws_server(self):
        """WebSocket 服务器"""
        if not WEBSOCKETS_AVAILABLE:
            print("WebSocket is not available. Install websockets library to enable.")
            return

        stop_event = asyncio.Event()

        async with serve(self._ws_handler, self.host, self.ws_port):
            print(f"WebSocket Server started on ws://{self.host}:{self.ws_port}/ws")
            await stop_event.wait()

    def _run_ws_server(self):
        """在独立线程中运行 WebSocket 服务器"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._ws_server())

    # =========================================================================
    # 启动
    # =========================================================================

    def start(self, blocking: bool = True, http_only: bool = False):
        """启动服务器

        Args:
            blocking: 是否阻塞主线程
            http_only: 是否仅启动 HTTP 服务器（不启动 WebSocket）
        """
        # HTTP 服务器
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self._handle()
            def do_POST(self):
                self._handle()
            def do_DELETE(self):
                self._handle()
            def do_PUT(self):
                self._handle()
            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, PUT, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
                self.end_headers()

            def do_HEAD(self):
                self.send_response(200)

            def _handle(self):
                import sys as _sys
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length else ""

                query_params = {}
                if "?" in self.path:
                    path, query = self.path.split("?", 1)
                    for param in query.split("&"):
                        if "=" in param:
                            k, v = param.split("=", 1)
                            query_params[k] = v
                else:
                    path = self.path

                # headers 字典键名为小写（email.message.EmailMessage 规范化的）
                raw_headers = dict(self.headers)
                if getattr(self.server.server, 'debug', False):
                    _sys.stderr.write(
                        f"[netapi DEBUG] headers.keys()={list(raw_headers.keys())} | "
                        f"x-api-key={raw_headers.get('x-api-key', '(none)')[:30]}\n"
                    )
                    _sys.stderr.flush()

                req = HTTPRequest(
                    method=self.command,
                    path=path,
                    headers=raw_headers,
                    body=body,
                    query_params=query_params,
                )

                response = self.server.server.handle_request(req)
                self.wfile.write(response.to_bytes())

        class TCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            allow_reuse_address = True

        http_server = TCPServer((self.host, self.port), Handler)
        http_server.server = self

        if blocking:
            print(f"HTTP API Server started on http://{self.host}:{self.port}")
            if not http_only and WEBSOCKETS_AVAILABLE:
                print(f"WebSocket Server started on ws://{self.host}:{self.ws_port}/ws")

            # 启动 WebSocket 服务器
            if not http_only and WEBSOCKETS_AVAILABLE:
                self._ws_server_thread = threading.Thread(target=self._run_ws_server, daemon=True)
                self._ws_server_thread.start()

            print("Press Ctrl+C to stop")
            try:
                http_server.serve_forever()
            except KeyboardInterrupt:
                print("\nShutting down...")
                http_server.shutdown()
        else:
            self._http_server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            self._http_server_thread.start()

            if not http_only and WEBSOCKETS_AVAILABLE:
                self._ws_server_thread = threading.Thread(target=self._run_ws_server, daemon=True)
                self._ws_server_thread.start()

            return self._http_server_thread

    def stop(self):
        """停止服务器"""
        # 清理工作（未来可扩展）"""