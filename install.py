# -*- coding: utf-8 -*-
"""
星尘记忆系统 — 一键安装脚本
用法: python setup.py
"""

import secrets
import subprocess
import sys
from pathlib import Path
from typing import List


ROOT = Path(__file__).parent
VENV_DIR = ROOT / "venv"

# Torch 安装命令（CUDA 12.8）
TORCH_INSTALL = (
    "pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 "
    "-f https://mirrors.aliyun.com/pytorch-wheels/cu128 "
    "-f https://download.pytorch.org/whl/cu128"
)


def run(cmd: str, desc: str = "") -> None:
    print(f"\n{'=' * 60}")
    print(f">>> {desc or cmd}")
    print("=" * 60)
    result = subprocess.run(cmd, shell=True, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\n[ERROR] 命令失败 (code={result.returncode}): {cmd}")
        sys.exit(result.returncode)


def get_python() -> str:
    """返回 venv 中的 python 路径"""
    return str(VENV_DIR / "Scripts" / "python.exe")


CONFIG_FILE = ROOT / "config.toml"
CHAT_CONFIG_FILE = ROOT / "chat" / "config.toml"

DEFAULT_CHAT_CONFIG = """# ── 用户设置 ──
[user]
name = "你"

# ── 星辰记忆数据库 ──
[memory]
base_url = "http://127.0.0.1:8080"
api_key = ""

# ── LLM（OpenAI 兼容）──
[llm]
base_url = "https://api.deepseek.com"
api_key = ""
model = "deepseek-v4-flash"
max_tokens = 10000
temperature = 0.8

# ── 思考/记忆设置 ──
[monologue]
thinking_style = "inner_os"
recall_triggers = ["回忆一下", "回忆", "回想", "记忆中"]
remember_triggers = ["记住", "记住这个", "铭记", "记下来", "要记得"]
max_segments = 50
max_think_rounds = 5
recall_top_k = 5
prefetch_top_k = 3

# ── 角色卡 ──
[character]
default = "example"
"""


def _generate_server_config() -> None:
    if CONFIG_FILE.exists():
        print(f"[SKIP] 服务器配置已存在: {CONFIG_FILE}")
        return
    api_key = f"xi-admin-{secrets.token_urlsafe(32)}"
    lines = [
        "# xcmemory_interest 配置文件",
        "# 由 install.py 自动生成，请勿手动删除 api_key（否则需重新生成）",
        "",
        "[server]",
        "database_root = './data/xcmemory'",
        "host = '0.0.0.0'",
        "port = 8080",
        "ws_port = 8081",
        "",
        "[lifecycle_manager]",
        "infinity = 999999",
        "short_term_cap = 604800",
        "long_term_cap = 2592000",
        "transition_cap = 31536000",
        "min_scale = 0.1",
        "",
        "[netapi]",
        "guid = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'",
        "max_websocket_frame_size = 65536",
        "",
        "[admin]",
        f"api_key = {api_key!r}",
        "",
        "[openai]",
        "api_key = nil  # 首次启动时请设置 api_key",
        "base_url = 'https://openrouter.ai/api/v1'",
        "model = 'xiaomi/mimo-v2-flash'",
        "",
    ]
    CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 已生成服务器配置: {CONFIG_FILE}")


def _generate_chat_config() -> None:
    if CHAT_CONFIG_FILE.exists():
        print(f"[SKIP] Chat 配置已存在: {CHAT_CONFIG_FILE}")
        return
    CHAT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_CONFIG_FILE.write_text(DEFAULT_CHAT_CONFIG, encoding="utf-8")
    print(f"[OK] 已生成 Chat 配置: {CHAT_CONFIG_FILE}")


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   星尘记忆系统 v0.4.0  一键安装         ║")
    print("╚══════════════════════════════════════════╝")

    # ── Step 1: 创建虚拟环境 ──
    if not VENV_DIR.exists():
        run(f"{sys.executable} -m venv venv", "创建虚拟环境")
    else:
        print("虚拟环境已存在，跳过创建")

    python = get_python()

    # ── Step 2: 升级 pip ──
    run(f"{python} -m pip install --upgrade pip", "升级 pip")

    # ── Step 3: 安装项目核心依赖 ──
    run(f"{python} -m pip install -e .", "安装 xcmemory_interest 核心依赖")

    # ── Step 4: 安装 Torch ──
    run(f"{python} -m {TORCH_INSTALL}", "安装 PyTorch (阿里云镜像, CUDA 12.8)")

    # ── Step 5: 安装 chat 应用依赖 ──
    chat_req = ROOT / "chat" / "requirements.txt"
    if chat_req.exists():
        run(f"{python} -m pip install -r {chat_req}", "安装 chat 应用依赖")

    # ── Step 6: 生成配置文件（不覆盖已有）──
    _generate_server_config()
    _generate_chat_config()

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("✔  安装完成！")
    print("=" * 60)
    print(f"""
启动命令：
  启动服务器:     {python} start_server.py
  启动服务器(UI): {python} start_server.py --gradio
  免 Torch 启动:  {python} start_server_notorch.py
  启动 Chat:      {python} chat/main.py --character example
""")


if __name__ == "__main__":
    main()
