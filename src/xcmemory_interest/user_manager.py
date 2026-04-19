"""
API 鉴权与用户权限管理系统

功能：
- APIKey 认证：xi-<username>-<api_key> 格式
- 用户权限管理：记忆系统级别的读写权限 + 版本控制权限
- 超级管理员（admin）：拥有所有权限和用户管理权限

存储结构：
<database_root>/
  auth.db          # SQLite 用户和权限数据库
  <system_name>/   # 各记忆系统目录
    vec_db/
    aux_db/
"""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any


# ============================================================================
# 常量与枚举
# ============================================================================

class PermissionType(Enum):
    """权限类型"""
    READ = "read"           # 只读
    WRITE = "write"         # 只写
    READ_WRITE = "read_write"  # 读写
    # 版本控制权限
    VERSION_COMMIT = "version_commit"   # 提交版本
    VERSION_DELETE = "version_delete"   # 删除版本
    # 管理员权限
    ADMIN = "admin"         # 用户系统管理员（仅超级管理员拥有）


class PermissionScope(Enum):
    """权限范围"""
    SYSTEM = "system"   # 记忆系统权限
    USER = "user"       # 用户管理权限


@dataclass
class User:
    """用户"""
    id: int
    username: str
    api_key_hash: str
    is_superadmin: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class Permission:
    """权限记录"""
    id: int
    username: str
    system_name: str
    permission: PermissionType
    granted_at: datetime
    granted_by: str


@dataclass
class AuthResult:
    """认证结果"""
    success: bool
    username: Optional[str] = None
    user: Optional[User] = None
    error: Optional[str] = None
    permissions: List[Permission] = field(default_factory=list)


# ============================================================================
# UserManager - 用户与权限管理
# ============================================================================

class UserManager:
    """
    用户与 APIKey 认证管理器

    APIKey 格式：xi-<username>-<api_key>
    存储方式：hash(api_key)，不存储明文
    """

    DEFAULT_ADMIN = "admin"

    def __init__(self, database_root: str):
        """
        初始化 UserManager

        Args:
            database_root: 数据库根目录（记忆数据库根目录）
        """
        self.database_root = Path(database_root)
        self.database_root.mkdir(parents=True, exist_ok=True)
        self.auth_db_path = self.database_root / "auth.db"
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.auth_db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_connection()
        try:
            # 用户表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    api_key_hash TEXT NOT NULL,
                    is_superadmin INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 权限表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    system_name TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    granted_by TEXT,
                    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE,
                    UNIQUE(username, system_name, permission)
                )
            """)

            # 创建默认管理员（如果不存在）
            now = datetime.now().isoformat()
            cursor = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (self.DEFAULT_ADMIN,)
            )
            if cursor.fetchone() is None:
                conn.execute(
                    "INSERT INTO users (username, api_key_hash, is_superadmin, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
                    (self.DEFAULT_ADMIN, "", now, now)
                )

            conn.commit()
        finally:
            conn.close()

    # =========================================================================
    # APIKey 管理
    # =========================================================================

    @staticmethod
    def _hash_api_key(api_key: str) -> str:
        """对 APIKey 进行哈希"""
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def _verify_api_key(api_key: str, stored_hash: str) -> bool:
        """验证 APIKey"""
        if not stored_hash:
            return False
        return UserManager._hash_api_key(api_key) == stored_hash

    def generate_api_key(self, username: str) -> str:
        """
        为用户生成新的 APIKey

        Args:
            username: 用户名

        Returns:
            生成的 APIKey（格式：xi-<username>-<random_key>）
        """
        random_key = secrets.token_urlsafe(32)
        api_key = f"xi-{username}-{random_key}"
        api_key_hash = self._hash_api_key(api_key)

        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE users SET api_key_hash = ?, updated_at = ? WHERE username = ?",
                (api_key_hash, datetime.now().isoformat(), username)
            )
            conn.commit()
        finally:
            conn.close()

        return api_key

    def revoke_api_key(self, username: str) -> bool:
        """
        吊销用户的 APIKey

        Args:
            username: 用户名

        Returns:
            是否成功
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "UPDATE users SET api_key_hash = '', updated_at = ? WHERE username = ?",
                (datetime.now().isoformat(), username)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def set_admin_api_key(self, api_key: str) -> None:
        """
        设置超级管理员的 APIKey（用于首次启动时自动生成密钥）。

        Args:
            api_key: 完整的 APIKey（格式：xi-admin-<random>）
        """
        api_key_hash = self._hash_api_key(api_key)
        now = datetime.now().isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE users SET api_key_hash = ?, updated_at = ? WHERE username = ?",
                (api_key_hash, now, self.DEFAULT_ADMIN)
            )
            conn.commit()
        finally:
            conn.close()

    # =========================================================================
    # 用户认证
    # =========================================================================

    def authenticate(self, api_key: str) -> AuthResult:
        """
        认证 APIKey

        Args:
            api_key: 完整 APIKey（格式：xi-<username>-<api_key>）

        Returns:
            AuthResult: 认证结果
        """
        # 解析格式
        if not api_key.startswith("xi-"):
            return AuthResult(success=False, error="Invalid APIKey format. Expected: xi-<username>-<api_key>")

        try:
            _, username, key_part = api_key.split("-", 2)
        except ValueError:
            return AuthResult(success=False, error="Invalid APIKey format. Expected: xi-<username>-<api_key>")

        if not username or not key_part:
            return AuthResult(success=False, error="Invalid APIKey format")

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()

            if row is None:
                return AuthResult(success=False, error=f"User '{username}' not found")

            user = User(
                id=row["id"],
                username=row["username"],
                api_key_hash=row["api_key_hash"],
                is_superadmin=bool(row["is_superadmin"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

            # 超级管理员直接通过（允许空 hash 的默认 admin）
            if user.is_superadmin and not user.api_key_hash:
                permissions = self._get_user_permissions(conn, username)
                return AuthResult(
                    success=True,
                    username=username,
                    user=user,
                    permissions=permissions,
                )

            # 验证 APIKey
            if not self._verify_api_key(api_key, user.api_key_hash):
                return AuthResult(success=False, error="Invalid APIKey")

            permissions = self._get_user_permissions(conn, username)
            return AuthResult(
                success=True,
                username=username,
                user=user,
                permissions=permissions,
            )
        finally:
            conn.close()

    def _get_user_permissions(self, conn: sqlite3.Connection, username: str) -> List[Permission]:
        """获取用户的所有权限"""
        cursor = conn.execute(
            "SELECT * FROM permissions WHERE username = ?",
            (username,)
        )
        permissions = []
        for row in cursor.fetchall():
            permissions.append(Permission(
                id=row["id"],
                username=row["username"],
                system_name=row["system_name"],
                permission=PermissionType(row["permission"]),
                granted_at=datetime.fromisoformat(row["granted_at"]),
                granted_by=row["granted_by"] or "",
            ))
        return permissions

    # =========================================================================
    # 用户管理（仅超级管理员）
    # =========================================================================

    def create_user(self, username: str, api_key: str = None) -> tuple:
        """
        创建用户

        Args:
            username: 用户名
            api_key: 可选的初始 APIKey（如果不提供，将生成）

        Returns:
            (success, api_key_or_error): 成功返回 (True, api_key)，失败返回 (False, error_message)
        """
        if not username or len(username) < 2:
            return False, "Username must be at least 2 characters"

        if username == self.DEFAULT_ADMIN:
            return False, f"Cannot create user with reserved username '{self.DEFAULT_ADMIN}'"

        conn = self._get_connection()
        try:
            # 检查是否已存在
            cursor = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
            if cursor.fetchone():
                return False, f"User '{username}' already exists"

            # 生成或使用提供的 APIKey
            if api_key:
                if not api_key.startswith(f"xi-{username}-"):
                    api_key = f"xi-{username}-{api_key}"
                api_key_hash = self._hash_api_key(api_key)
            else:
                api_key = self.generate_api_key(username)
                api_key_hash = self._hash_api_key(api_key)

            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO users (username, api_key_hash, is_superadmin, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
                (username, api_key_hash, now, now)
            )
            conn.commit()
            return True, api_key
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    def delete_user(self, username: str) -> tuple:
        """
        删除用户（仅超级管理员）

        Args:
            username: 用户名

        Returns:
            (success, message)
        """
        if username == self.DEFAULT_ADMIN:
            return False, f"Cannot delete superadmin '{self.DEFAULT_ADMIN}'"

        conn = self._get_connection()
        try:
            cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            if cursor.rowcount > 0:
                return True, f"User '{username}' deleted"
            else:
                return False, f"User '{username}' not found"
        finally:
            conn.close()

    def list_users(self) -> List[Dict[str, Any]]:
        """列出所有用户"""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT u.*,
                       GROUP_CONCAT(p.system_name || ':' || p.permission) as permissions
                FROM users u
                LEFT JOIN permissions p ON u.username = p.username
                GROUP BY u.username
            """)
            users = []
            for row in cursor.fetchall():
                perms = []
                if row["permissions"]:
                    for p in row["permissions"].split(","):
                        if ":" in p:
                            sys, perm = p.split(":", 1)
                            perms.append({"system": sys, "permission": perm})
                users.append({
                    "id": row["id"],
                    "username": row["username"],
                    "is_superadmin": bool(row["is_superadmin"]),
                    "has_api_key": bool(row["api_key_hash"]),
                    "created_at": row["created_at"],
                    "permissions": perms,
                })
            return users
        finally:
            conn.close()

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """获取用户详情"""
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            if row is None:
                return None

            perms = self._get_user_permissions(conn, username)
            return {
                "id": row["id"],
                "username": row["username"],
                "is_superadmin": bool(row["is_superadmin"]),
                "has_api_key": bool(row["api_key_hash"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "permissions": [
                    {"system": p.system_name, "permission": p.permission.value}
                    for p in perms
                ],
            }
        finally:
            conn.close()

    # =========================================================================
    # 权限管理
    # =========================================================================

    def grant_permission(
        self,
        username: str,
        system_name: str,
        permission: PermissionType,
        granted_by: str = None,
    ) -> tuple:
        """
        授予权限

        Args:
            username: 用户名
            system_name: 记忆系统名称（'*' 表示所有系统）
            permission: 权限类型
            granted_by: 授权者

        Returns:
            (success, message)
        """
        conn = self._get_connection()
        try:
            # 检查用户是否存在
            cursor = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
            if not cursor.fetchone():
                return False, f"User '{username}' not found"

            now = datetime.now().isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO permissions (username, system_name, permission, granted_at, granted_by)
                VALUES (?, ?, ?, ?, ?)
            """, (username, system_name, permission.value, now, granted_by))
            conn.commit()
            return True, f"Granted {permission.value} on '{system_name}' to '{username}'"
        finally:
            conn.close()

    def revoke_permission(
        self,
        username: str,
        system_name: str,
        permission: PermissionType,
    ) -> tuple:
        """
        撤销权限

        Args:
            username: 用户名
            system_name: 记忆系统名称
            permission: 权限类型

        Returns:
            (success, message)
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                DELETE FROM permissions
                WHERE username = ? AND system_name = ? AND permission = ?
            """, (username, system_name, permission.value))
            conn.commit()
            if cursor.rowcount > 0:
                return True, f"Revoked {permission.value} on '{system_name}' from '{username}'"
            else:
                return False, f"Permission not found"
        finally:
            conn.close()

    def has_permission(
        self,
        username: str,
        system_name: str,
        permission: PermissionType,
    ) -> bool:
        """
        检查用户是否拥有特定权限

        Args:
            username: 用户名
            system_name: 记忆系统名称
            permission: 权限类型

        Returns:
            是否拥有权限
        """
        conn = self._get_connection()
        try:
            # 检查超级管理员
            cursor = conn.execute(
                "SELECT is_superadmin FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()
            if row and bool(row["is_superadmin"]):
                return True

            # 检查具体权限（包括 '*' 通配符）
            cursor = conn.execute("""
                SELECT id FROM permissions
                WHERE username = ?
                AND (system_name = ? OR system_name = '*')
                AND permission = ?
            """, (username, system_name, permission.value))
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def get_user_systems_permissions(self, username: str) -> Dict[str, List[str]]:
        """
        获取用户对各记忆系统的权限

        Returns:
            {system_name: [permission_types], ...}
        """
        conn = self._get_connection()
        try:
            # 检查超级管理员
            cursor = conn.execute(
                "SELECT is_superadmin FROM users WHERE username = ?",
                (username,)
            )
            row = cursor.fetchone()
            if row and bool(row["is_superadmin"]):
                return {"*": ["*"]}  # 所有权限

            cursor = conn.execute("""
                SELECT system_name, permission FROM permissions WHERE username = ?
            """, (username,))
            result: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                sys = row["system_name"]
                perm = row["permission"]
                if sys not in result:
                    result[sys] = []
                result[sys].append(perm)
            return result
        finally:
            conn.close()

    # =========================================================================
    # 权限检查装饰器
    # =========================================================================

    def check_permission(self, system_name: str, permission: PermissionType):
        """
        权限检查装饰器工厂

        Usage:
            @user_manager.check_permission("my_system", PermissionType.READ)
            def my_func(auth_context):
                ...

        Args:
            system_name: 记忆系统名称（或 None 表示不需要系统权限）
            permission: 所需权限类型
        """
        def decorator(func):
            def wrapper(auth_context: Dict = None, *args, **kwargs):
                if auth_context is None:
                    auth_context = {}

                username = auth_context.get("username")
                if not username:
                    raise PermissionError("Authentication required")

                # 如果需要系统权限
                if system_name and not self.has_permission(username, system_name, permission):
                    raise PermissionError(
                        f"User '{username}' does not have '{permission.value}' permission on '{system_name}'"
                    )

                return func(auth_context, *args, **kwargs)
            return wrapper
        return decorator

    # =========================================================================
    # 工具方法
    # =========================================================================

    def list_systems(self) -> List[str]:
        """列出所有有权限的记忆系统"""
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
                SELECT DISTINCT system_name FROM permissions
                WHERE system_name != '*'
            """)
            return [row["system_name"] for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        conn = self._get_connection()
        try:
            user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            perm_count = conn.execute("SELECT COUNT(*) as c FROM permissions").fetchone()["c"]
            return {
                "total_users": user_count,
                "total_permissions": perm_count,
            }
        finally:
            conn.close()


# ============================================================================
# AuthContext - 认证上下文（传递给操作函数）
# ============================================================================

@dataclass
class AuthContext:
    """认证上下文"""
    username: str
    is_superadmin: bool
    permissions: Dict[str, List[PermissionType]]  # {system_name: [permissions]}

    @staticmethod
    def from_auth_result(auth_result: AuthResult) -> "AuthContext":
        """从认证结果创建上下文"""
        if not auth_result.success:
            raise PermissionError(auth_result.error or "Authentication failed")

        perms: Dict[str, List[PermissionType]] = {}
        for p in auth_result.permissions:
            if p.system_name not in perms:
                perms[p.system_name] = []
            perms[p.system_name].append(p.permission)

        return AuthContext(
            username=auth_result.username,
            is_superadmin=auth_result.user.is_superadmin if auth_result.user else False,
            permissions=perms,
        )

    def has_permission(self, system_name: str, permission: PermissionType) -> bool:
        """检查是否有权限"""
        if self.is_superadmin:
            return True
        # 检查具体系统权限
        if system_name in self.permissions:
            if permission.value in [p.value for p in self.permissions[system_name]]:
                return True
        # 检查通配符权限
        if "*" in self.permissions:
            if permission.value in [p.value for p in self.permissions["*"]]:
                return True
            # 通配符系统的读权限
            if "*" in self.permissions["*"]:
                return True
        return False

    def has_system_access(self, system_name: str, require_write: bool = False) -> bool:
        """检查是否有系统访问权限"""
        if self.is_superadmin:
            return True
        if system_name in self.permissions:
            perms = [p.value for p in self.permissions[system_name]]
            if require_write:
                return "read_write" in perms or "write" in perms
            else:
                return len(perms) > 0
        return False