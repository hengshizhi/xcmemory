# -*- coding: utf-8 -*-
"""
星尘记忆 Chat — 入口

用法:
    python main.py --character example
    python main.py --character example --config config.toml
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 确保 chat 目录在 import 路径中
CHAT_DIR = Path(__file__).parent
sys.path.insert(0, str(CHAT_DIR))


def load_config(config_path: str) -> dict:
    """加载 TOML 配置文件"""
    p = Path(config_path)
    if not p.exists():
        print(f"❌ 配置文件不存在: {p}")
        sys.exit(1)

    try:
        # Python 3.11+ 内置 tomllib
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(p, "rb") as f:
        return tomllib.load(f)


def main():
    parser = argparse.ArgumentParser(description="星尘记忆 Chat")
    parser.add_argument(
        "--character", default="example",
        help="角色卡名称（对应 characters/ 目录下的 YAML 文件）",
    )
    parser.add_argument(
        "--config", default=str(CHAT_DIR / "config.toml"),
        help="配置文件路径",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="显示记忆管家上下文等调试信息",
    )
    args = parser.parse_args()

    # 1. 加载配置
    config = load_config(args.config)

    # 2. 加载角色卡
    from character_card import CharacterCard
    char_path = CHAT_DIR / "characters" / f"{args.character}.yaml"
    try:
        character = CharacterCard.load(str(char_path))
    except FileNotFoundError:
        print(f"❌ 角色卡不存在: {char_path}")
        sys.exit(1)
    except ValueError as e:
        print(f"❌ 角色卡格式错误: {e}")
        sys.exit(1)

    # 3. 获取 system_name（优先从角色卡读取）
    system_name = character.system_name
    print(f"📋 角色: {character.name}")
    print(f"📋 记忆系统: {system_name}")

    # 4. 获取用户名
    user_name = config.get("user", {}).get("name", "你")
    print(f"📋 用户: {user_name}")

    # 5. 初始化客户端
    from memory_client import MemoryClient
    from llm_client import LLMClient

    mem_cfg = config.get("memory", {})
    llm_cfg = config.get("llm", {})

    memory = MemoryClient(
        base_url=mem_cfg.get("base_url", "http://127.0.0.1:8080"),
        api_key=mem_cfg.get("api_key", ""),
        system_name=system_name,
    )

    llm = LLMClient(
        base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        api_key=llm_cfg.get("api_key", ""),
        model=llm_cfg.get("model", "deepseek-v4-flash"),
        max_tokens=llm_cfg.get("max_tokens", 2048),
        temperature=llm_cfg.get("temperature", 0.8),
    )

    # 6. 创建引擎
    from chat_engine import ChatEngine
    engine = ChatEngine(character, llm, memory, config, user_name=user_name)

    # 7. 启动 UI
    from ui.terminal_ui import TerminalUI
    ui = TerminalUI(engine, character, debug=args.debug)
    asyncio.run(ui.run())


if __name__ == "__main__":
    main()
