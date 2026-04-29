# -*- coding: utf-8 -*-
"""
星尘记忆系统 — 一键安装脚本
用法: python setup.py
"""

import subprocess
import sys
from pathlib import Path
from typing import List


ROOT = Path(__file__).parent
VENV_DIR = ROOT / "venv"

# Torch 安装命令（阿里云镜像，CUDA 12.8）
TORCH_INSTALL = (
    "pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 "
    "-f https://mirrors.aliyun.com/pytorch-wheels/cu128"
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

    # ── Step 4: 安装 Torch（可选，AI 模型需要）──
    answer = input("\n是否安装 PyTorch？(y/N): ").strip().lower()
    if answer == "y":
        run(f"{python} -m {TORCH_INSTALL}", "安装 PyTorch (阿里云镜像)")
    else:
        print("跳过 PyTorch 安装（可用 start_server_notorch.py 启动）")

    # ── Step 5: 安装 chat 应用依赖 ──
    chat_req = ROOT / "chat" / "requirements.txt"
    if chat_req.exists():
        run(f"{python} -m pip install -r {chat_req}", "安装 chat 应用依赖")

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("✔  安装完成！")
    print("=" * 60)
    print(f"""
启动命令：
  启动服务器:    {python} start_server.py
  启动服务器(UI): {python} start_server.py --gradio
  免 Torch 启动:  {python} start_server_notorch.py
  启动 Chat:      {python} chat/main.py --character example

配置文件：
  服务器配置:    config.toml（首次启动自动生成）
  Chat 配置:     chat/config.toml（首次启动自动生成）
""")


if __name__ == "__main__":
    main()
