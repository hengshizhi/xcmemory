# -*- coding: utf-8 -*-
"""
终端 UI — MVP 版本

提供终端交互界面，展示自白过程和对话回复。
"""

import asyncio
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

from chat_engine import ChatEngine, ChatEvent, EventType
from character_card import CharacterCard


class TerminalUI:
    """终端 UI"""

    def __init__(self, engine: ChatEngine, character: CharacterCard):
        self.engine = engine
        self.character = character
        self.console = Console()

    async def run(self):
        """启动终端交互"""
        self._print_banner()

        # 健康检查
        online = await self.engine.memory.health_check()
        if online:
            self.console.print("[green]✅ 记忆系统连接成功[/green]")
        else:
            self.console.print("[red]❌ 记忆系统连接失败，请检查服务是否启动[/red]")
            self.console.print("[dim]   将以无记忆模式运行[/dim]")

        # 确保记忆系统已激活
        if online:
            ensured = await self.engine.memory.ensure_system()
            if ensured:
                self.console.print(f"[green]✅ 记忆系统 '{self.engine.memory.system_name}' 已激活[/green]")
            else:
                self.console.print("[yellow]⚠️ 记忆系统激活失败[/yellow]")

        # 检查记忆是否为空，触发引导流程
        if online:
            count = await self.engine.memory.count_memories()
            if count == 0:
                self.console.print()
                self.console.print("[bold yellow]🌟 检测到记忆系统为空，进入引导模式...[/bold yellow]")
                self.console.print("[dim]在引导模式中，角色将通过与你的对话逐步了解自己，并自动写入记忆。[/dim]")
                self.console.print()
                await self._run_onboarding()
                self.console.print("[bold green]✅ 引导完成！进入正常对话模式[/bold green]\n")
            elif count > 0:
                self.console.print(f"[green]📚 记忆系统已有 {count} 条记忆[/green]")
            else:
                self.console.print("[yellow]⚠️ 无法获取记忆数量[/yellow]")

        self.console.print()

        # 主循环
        while True:
            try:
                user_input = self.console.input(
                    f"[bold cyan]{self.engine.user_name}:[/bold cyan] "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]再见~[/dim]")
                break

            if not user_input:
                continue

            # 特殊命令
            if user_input in ("/quit", "/exit", "/q"):
                self.console.print("[dim]再见~[/dim]")
                break
            elif user_input == "/clear":
                self.engine.clear_history()
                self.console.print("[dim]对话历史已清除[/dim]\n")
                continue
            elif user_input == "/history":
                rounds = self.engine.get_history_length()
                self.console.print(f"[dim]当前 {rounds} 轮对话[/dim]\n")
                continue
            elif user_input.startswith("/system "):
                system_name = user_input[8:].strip()
                self.engine.memory.system_name = system_name
                ensured = await self.engine.memory.ensure_system()
                if ensured:
                    self.console.print(f"[green]✅ 已切换到系统 '{system_name}'[/green]\n")
                else:
                    self.console.print(f"[red]❌ 切换系统 '{system_name}' 失败[/red]\n")
                continue
            elif user_input == "/help":
                self._print_help()
                continue

            # 正常对话
            await self._handle_chat(user_input)

    async def _run_onboarding(self):
        """运行引导流程：角色通过与用户交流了解自己"""
        self.console.print("[bold]引导开始！请与角色对话，帮助她了解自己。[/bold]")
        self.console.print("[dim]你可以告诉她关于她的身份、性格、经历等信息。[/dim]")
        self.console.print("[dim]输入 /done 结束引导进入正常对话。[/dim]\n")

        # 引导首轮：角色先自我介绍
        self.console.print("[dim]角色正在思考自己的身份...[/dim]")
        await self._handle_onboarding_turn("你好，我是谁？能告诉我一些关于我的事情吗？")

        while True:
            try:
                user_input = self.console.input(
                    f"[bold cyan]{self.engine.user_name}:[/bold cyan] "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            if user_input == "/done":
                self.console.print("[dim]引导结束[/dim]")
                break
            elif user_input == "/quit" or user_input == "/exit" or user_input == "/q":
                return

            await self._handle_onboarding_turn(user_input)

    async def _handle_onboarding_turn(self, user_input: str):
        """处理引导模式的一轮对话"""
        monologue_parts = []
        reply_parts = []

        try:
            async for event in self.engine.onboarding_chat(user_input):
                if event.type == EventType.MONOLOGUE_START:
                    monologue_parts = []
                elif event.type == EventType.MONOLOGUE_SEGMENT:
                    monologue_parts.append(event.text)
                elif event.type == EventType.MEMORY_RECALL:
                    self.console.print(f"  [dim yellow]{event.text}[/dim yellow]")
                elif event.type == EventType.MEMORY_WRITE:
                    self.console.print(f"  [dim green]{event.text}[/dim green]")
                elif event.type == EventType.MONOLOGUE_END:
                    if monologue_parts:
                        mono_text = "\n".join(monologue_parts)
                        self.console.print()
                        self.console.print(
                            Panel(
                                Text(mono_text, style="dim italic"),
                                title=f"[{self.character.avatar}] {self.character.name}的内心",
                                border_style="dim",
                                padding=(0, 1),
                            )
                        )
                elif event.type == EventType.REPLY_SEGMENT:
                    reply_parts.append(event.text)
                elif event.type == EventType.REPLY_END:
                    if reply_parts:
                        reply_text = "".join(reply_parts)
                        self.console.print()
                        self.console.print(
                            f"[bold {self._character_color()}]"
                            f"{self.character.avatar} {self.character.name}:[/bold {self._character_color()}] "
                            f"{reply_text}"
                        )
                elif event.type == EventType.ERROR:
                    self.console.print(f"[red]{event.text}[/red]")

        except Exception as e:
            self.console.print(f"[red]错误: {e}[/red]")

        # 保存历史
        self.engine.save_to_history(user_input)
        self.console.print()

    async def _handle_chat(self, user_input: str):
        """处理一次对话"""
        monologue_parts = []
        reply_parts = []

        try:
            async for event in self.engine.chat(user_input):
                if event.type == EventType.MONOLOGUE_START:
                    monologue_parts = []
                elif event.type == EventType.MONOLOGUE_SEGMENT:
                    monologue_parts.append(event.text)
                elif event.type == EventType.MEMORY_RECALL:
                    self.console.print(f"  [dim yellow]{event.text}[/dim yellow]")
                elif event.type == EventType.MEMORY_WRITE:
                    self.console.print(f"  [dim green]{event.text}[/dim green]")
                elif event.type == EventType.MONOLOGUE_END:
                    if monologue_parts:
                        mono_text = "\n".join(monologue_parts)
                        self.console.print()
                        self.console.print(
                            Panel(
                                Text(mono_text, style="dim italic"),
                                title=f"[{self.character.avatar}] {self.character.name}的内心",
                                border_style="dim",
                                padding=(0, 1),
                            )
                        )
                elif event.type == EventType.REPLY_SEGMENT:
                    reply_parts.append(event.text)
                elif event.type == EventType.REPLY_END:
                    if reply_parts:
                        reply_text = "".join(reply_parts)
                        self.console.print()
                        self.console.print(
                            f"[bold {self._character_color()}]"
                            f"{self.character.avatar} {self.character.name}:[/bold {self._character_color()}] "
                            f"{reply_text}"
                        )
                elif event.type == EventType.ERROR:
                    self.console.print(f"[red]{event.text}[/red]")

        except Exception as e:
            self.console.print(f"[red]错误: {e}[/red]")

        self.engine.save_to_history(user_input)
        self.console.print()

    def _character_color(self) -> str:
        """角色名颜色"""
        return "magenta"

    def _print_banner(self):
        """打印启动横幅"""
        self.console.print()
        self.console.print(
            Panel(
                f"[bold]{self.character.avatar} 星尘记忆 Chat[/bold]\n\n"
                f"角色: {self.character.name}\n"
                f"用户: {self.engine.user_name}\n"
                f"记忆系统: {self.engine.memory.system_name}\n"
                f"记忆: {self.engine.memory.base_url}\n"
                f"LLM: {self.engine.llm.model}",
                border_style="bright_blue",
                padding=(1, 2),
            )
        )
        self.console.print()

    def _print_help(self):
        """打印帮助信息"""
        help_text = """
[bold]命令列表:[/bold]
  /quit, /exit, /q  — 退出
  /clear            — 清除对话历史
  /history          — 查看对话轮数
  /system <name>    — 切换记忆系统
  /help             — 显示此帮助
"""
        self.console.print(help_text)
