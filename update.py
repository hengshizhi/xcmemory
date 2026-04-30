# -*- coding: utf-8 -*-
"""
星尘记忆系统 — 更新脚本
从 GitHub 拉取最新代码并重新安装依赖
用法: python update.py
"""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent
VENV_DIR = ROOT / "venv"


def run(cmd: str, desc: str = "") -> None:
    print(f"\n{'=' * 60}")
    print(f">>> {desc or cmd}")
    print("=" * 60)
    result = subprocess.run(cmd, shell=True, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\n[ERROR] 命令失败 (code={result.returncode}): {cmd}")
        sys.exit(result.returncode)


def get_python() -> str:
    return str(VENV_DIR / "Scripts" / "python.exe")


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   星尘记忆系统 v0.4.0  更新             ║")
    print("╚══════════════════════════════════════════╝")

    # ── Step 1: 检查本地修改 ──
    result = subprocess.run(
        "git status --porcelain", shell=True, cwd=str(ROOT), capture_output=True, text=True
    )
    if result.stdout.strip():
        print("\n[WARN] 检测到未提交的本地修改：")
        print(result.stdout)
        answer = input("是否先 stash 再拉取？(y/N): ").strip().lower()
        if answer == "y":
            run("git stash", "暂存本地修改 (git stash)")
        else:
            print("[SKIP] 跳过本地修改，直接拉取（可能会有冲突）")

    # ── Step 2: 拉取最新代码 ──
    run("git pull", "从 GitHub 拉取最新代码")

    # ── Step 3: 重新安装核心依赖 ──
    python = get_python()
    run(f"{python} -m pip install --upgrade pip", "升级 pip")
    run(f"{python} -m pip install -e .", "更新 xcmemory_interest 核心依赖")

    # ── Step 4: 重新安装 chat 依赖 ──
    chat_req = ROOT / "chat" / "requirements.txt"
    if chat_req.exists():
        run(f"{python} -m pip install -r {chat_req}", "更新 chat 应用依赖")

    # ── 完成 ──
    print("\n" + "=" * 60)
    print("✔  更新完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
