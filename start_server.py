"""
xcmemory_interest 服务器启动脚本

功能：
- 检查配置文件（config.toml），不存在则自动创建
- 首次启动随机生成超级管理员 APIKey 并明文显示
- 启动 APIServer（HTTP + WebSocket）
- 支持通过命令行参数覆盖配置文件中的参数
- 可选启动 Gradio WebUI 管理面板（--gradio）
- Gradio 模式下 admin 免登录自动注入权限

配置文件 config.toml 包含：
- server: 数据库路径、HTTP/WebSocket 端口
- lifecycle_manager: 生命周期管理参数
- netapi: WebSocket 常量
- admin: 超级管理员 APIKey（首次自动生成）
"""

import os
import sys
import secrets
import argparse
import threading
from pathlib import Path

# 添加 src 目录到路径
SRC_ROOT = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_ROOT))

try:
    import tomllib
except ImportError:
    # Python < 3.11
    import tomli as tomllib


# ============================================================================
# 默认配置
# ============================================================================

DEFAULT_CONFIG = {
    "server": {
        "database_root": "./data/xcmemory",
        "host": "0.0.0.0",
        "port": 8080,
        "ws_port": 8081,
    },
    "lifecycle_manager": {
        "infinity": 999999,
        "short_term_cap": 7 * 86400,
        "long_term_cap": 30 * 86400,
        "transition_cap": 365 * 86400,
        "min_scale": 0.1,
    },
    "netapi": {
        "guid": "258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
        "max_websocket_frame_size": 65536,
    },
    "admin": {
        "api_key": None,  # 首次启动时自动生成
    },
}

CONFIG_FILE = Path(__file__).parent / "config.toml"


# ============================================================================
# 配置文件读写
# ============================================================================

def _generate_admin_api_key() -> str:
    """生成超级管理员 APIKey"""
    random_key = secrets.token_urlsafe(32)
    return f"xi-admin-{random_key}"


def _save_config(config: dict) -> None:
    """保存配置到 TOML 文件"""
    lines = []
    lines.append("# xcmemory_interest 配置文件")
    lines.append("# 由启动脚本自动生成，请勿手动删除 api_key（否则需重新生成）")
    lines.append("")
    lines.append("[server]")
    for k, v in config["server"].items():
        lines.append(f"{k} = {v!r}")
    lines.append("")
    lines.append("[lifecycle_manager]")
    for k, v in config["lifecycle_manager"].items():
        lines.append(f"{k} = {v!r}")
    lines.append("")
    lines.append("[netapi]")
    for k, v in config["netapi"].items():
        lines.append(f"{k} = {v!r}")
    lines.append("")
    lines.append("[admin]")
    if config["admin"]["api_key"]:
        lines.append(f"api_key = {config['admin']['api_key']!r}")
    else:
        lines.append("api_key = nil  # 首次启动时请设置 api_key")

    content = "\n".join(lines)
    CONFIG_FILE.write_text(content, encoding="utf-8")


def load_or_create_config() -> tuple[dict, bool]:
    """
    加载或创建配置文件。

    Returns:
        (config, is_first_boot): config 为配置字典，is_first_boot=True 表示首次启动
    """
    is_first_boot = False

    if not CONFIG_FILE.exists():
        is_first_boot = True
        config = dict(DEFAULT_CONFIG)
        config["admin"]["api_key"] = _generate_admin_api_key()
        _save_config(config)
        return config, is_first_boot

    # 加载现有配置
    content = CONFIG_FILE.read_text(encoding="utf-8")
    try:
        config = tomllib.loads(content)
    except Exception as e:
        print(f"[WARN] 配置文件解析失败 ({e})，将重新创建")
        os.remove(CONFIG_FILE)
        return load_or_create_config()

    # 补齐新增字段
    for section, defaults in DEFAULT_CONFIG.items():
        if section not in config:
            config[section] = dict(defaults)
        else:
            for k, v in defaults.items():
                if k not in config[section]:
                    config[section][k] = v

    # 检查 admin.api_key 是否为空
    if not config["admin"].get("api_key"):
        is_first_boot = True
        config["admin"]["api_key"] = _generate_admin_api_key()
        _save_config(config)

    return config, is_first_boot


# ============================================================================
# 配置应用到运行时
# ============================================================================

def apply_lifecycle_config(config: dict):
    """将配置文件中的 lifecycle_manager 参数应用到运行时常量"""
    from xcmemory_interest.lifecycle_manager import core as lc

    lm = config["lifecycle_manager"]
    lc.LIFECYCLE_INFINITY = lm["infinity"]
    lc.SHORT_TERM_CAP = lm["short_term_cap"]
    lc.LONG_TERM_CAP = lm["long_term_cap"]
    lc.TRANSITION_CAP = lm["transition_cap"]
    lc.MIN_SCALE = lm["min_scale"]


def apply_netapi_config(config: dict):
    """将配置文件中的 netapi 参数应用到运行时常量"""
    from xcmemory_interest import netapi

    nc = config["netapi"]
    netapi.GUID = nc["guid"]
    netapi.MAX_WEBSOCKET_FRAME_SIZE = nc["max_websocket_frame_size"]


def ensure_admin(config: dict):
    """
    确保超级管理员存在，若 api_key 为空则生成新的。
    返回 (api_key, is_new): 当前的 admin api_key，是否新生成
    """
    from xcmemory_interest.user_manager import UserManager

    db_root = config["server"]["database_root"]
    mgr = UserManager(db_root)

    api_key = config["admin"]["api_key"]
    is_new = False

    # 检查 admin 是否已有有效 api_key
    conn = mgr._get_connection()
    cursor = conn.execute(
        "SELECT api_key_hash FROM users WHERE username = ?",
        (mgr.DEFAULT_ADMIN,)
    )
    row = cursor.fetchone()
    conn.close()

    has_existing_key = row and row["api_key_hash"]

    if not has_existing_key:
        # admin 从未设置过 api_key（空的默认记录），设置或生成
        if not api_key:
            api_key = _generate_admin_api_key()
        is_new = True
        mgr.set_admin_api_key(api_key)
    else:
        # admin 已有 api_key；若 config.toml 有新 key 则同步更新
        if api_key:
            mgr.set_admin_api_key(api_key)
            is_new = True  # key 变化了，算作"新"
        else:
            pass  # 保持 DB 中已有的 key

    return api_key, is_new


# ============================================================================
# Gradio WebUI 线程启动
# ============================================================================

def _start_gradio_thread(api_server, admin_user: str, is_admin: bool, gradio_port: int):
    """在后台线程中启动 Gradio WebUI"""
    from webui.app import init_webui, launch_gradio

    # 注入已创建的 APIServer
    init_webui(api_server, admin_user, is_admin)

    # 启动 Gradio（阻塞）
    launch_gradio(gradio_port=gradio_port, pre_auth=True, admin_user=admin_user)


# ============================================================================
# 主启动逻辑
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="xcmemory_interest 服务器启动脚本")
    parser.add_argument("--host", type=str, default=None, help="覆盖 server.host")
    parser.add_argument("--port", type=int, default=None, help="覆盖 server.port")
    parser.add_argument("--ws-port", type=int, dest="ws_port", default=None, help="覆盖 server.ws_port")
    parser.add_argument("--db-root", type=str, dest="db_root", default=None, help="覆盖 server.database_root")
    parser.add_argument("--config", type=str, default=None, help="指定配置文件路径")
    parser.add_argument("--gradio", action="store_true", help="启动 Gradio WebUI（admin 免登录）")
    parser.add_argument("--gradio-port", type=int, default=7860, help="Gradio WebUI 端口 (默认 7860)")
    parser.add_argument("--debug", action="store_true", help="开启调试输出")
    args = parser.parse_args()

    global CONFIG_FILE
    if args.config:
        CONFIG_FILE = Path(args.config)

    # 加载/创建配置
    config, is_first_boot = load_or_create_config()

    # 命令行参数覆盖配置
    if args.host is not None:
        config["server"]["host"] = args.host
    if args.port is not None:
        config["server"]["port"] = args.port
    if args.ws_port is not None:
        config["server"]["ws_port"] = args.ws_port
    if args.db_root is not None:
        config["server"]["database_root"] = args.db_root

    # 应用运行时配置
    apply_lifecycle_config(config)
    apply_netapi_config(config)

    # 确保 admin 存在
    admin_api_key, admin_is_new = ensure_admin(config)

    # 打印启动信息
    print("=" * 60)
    print("  xcmemory_interest Server")
    print("=" * 60)
    print(f"  Config:    {CONFIG_FILE}")
    print(f"  Database:  {config['server']['database_root']}")
    print(f"  HTTP:      {config['server']['host']}:{config['server']['port']}")
    print(f"  WebSocket: {config['server']['host']}:{config['server']['ws_port']}")
    if args.gradio:
        print(f"  Gradio:   http://127.0.0.1:{args.gradio_port}/  (admin 免登录)")
    print("-" * 60)

    if is_first_boot or admin_is_new:
        print()
        print("  [FIRST BOOT] 超级管理员 APIKey 已生成：")
        print()
        print(f"    {admin_api_key}")
        print()
        print("  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print("  ^ 请妥善保存此密钥！关闭后无法再次显示。                    ^")
        print("  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
        print()
    else:
        print(f"  Admin APIKey: {'*' * 20} (已设置)")
        print()

    # 启动 API 服务器
    from xcmemory_interest.netapi import APIServer

    server = APIServer(
        database_root=config["server"]["database_root"],
        host=config["server"]["host"],
        port=config["server"]["port"],
        ws_port=config["server"]["ws_port"],
        debug=args.debug,
    )

    # Gradio WebUI（后台线程）
    if args.gradio:
        t = threading.Thread(
            target=_start_gradio_thread,
            args=(server, "admin", True, args.gradio_port),
            daemon=True,
        )
        t.start()
        print(f"  Gradio WebUI 启动中 http://127.0.0.1:{args.gradio_port}/ ...")
        print("=" * 60)
    else:
        print(f"  Starting server ...")
        print("=" * 60)

    server.start(blocking=True)


if __name__ == "__main__":
    main()
