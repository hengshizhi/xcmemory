# -*- coding: utf-8 -*-
"""
终端 UI
"""

import asyncio

from rich.console import Console
from rich.panel import Panel

from chat_engine import ChatEngine, ChatEvent, EventType
from character_card import CharacterCard


class TerminalUI:
    def __init__(self, engine: ChatEngine, character: CharacterCard, debug: bool = False):
        self.engine = engine
        self.character = character
        self.console = Console()
        self.debug = debug

    async def run(self):
        self._print_banner()

        online = await self.engine.memory.health_check()
        if online:
            self.console.print("[green]✅ 记忆系统连接成功[/green]")
        else:
            self.console.print("[red]❌ 记忆系统连接失败[/red]")
            self.console.print("[dim]   将以无记忆模式运行[/dim]")

        if online:
            ensured = await self.engine.memory.ensure_system()
            if ensured:
                self.console.print(f"[green]✅ 记忆系统 '{self.engine.memory.system_name}' 已激活[/green]")
            else:
                self.console.print("[yellow]⚠️ 记忆系统激活失败[/yellow]")

        if online:
            count = await self.engine.memory.count_memories()
            if count == 0:
                self.console.print()
                self.console.print("[bold yellow]🌟 检测到记忆系统为空[/bold yellow]")
                self.console.print("[dim]你可以直接与角色对话，记忆会在对话中逐步建立。[/dim]")
            elif count > 0:
                self.console.print(f"[green]📚 记忆系统已有 {count} 条记忆[/green]")
            else:
                self.console.print("[yellow]⚠️ 无法获取记忆数量[/yellow]")

        self.console.print()

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

            if user_input in ("/quit", "/exit", "/q"):
                self.console.print("[dim]再见~[/dim]")
                break
            elif user_input == "/clear":
                self.engine.clear_history()
                self.console.print("[dim]对话历史已清除[/dim]\n")
                continue
            elif user_input == "/help":
                self._print_help()
                continue

            await self._handle_chat(user_input)

    async def _handle_chat(self, user_input: str):
        reply_parts = []

        try:
            async for event in self.engine.chat(user_input):
                if event.type == EventType.MEMORY_QUERY:
                    self.console.print(f"  [dim blue]{event.text}[/dim blue]")
                elif event.type == EventType.MEMORY_RESULT:
                    self.console.print(f"  [dim green]📋 {event.text}[/dim green]")
                elif event.type == EventType.MEMORY_SAVE:
                    self.console.print(f"  [dim yellow]💾 {event.text}[/dim yellow]")
                elif event.type == EventType.REPLY_SEGMENT:
                    reply_parts.append(event.text)
                elif event.type == EventType.REPLY_END:
                    if reply_parts:
                        reply_text = "".join(reply_parts)
                        self.console.print()
                        self.console.print(
                            f"[bold magenta]"
                            f"{self.character.avatar} {self.character.name}:[/bold magenta] "
                            f"{reply_text}"
                        )
                elif event.type == EventType.ERROR:
                    self.console.print(f"[red]{event.text}[/red]")

        except Exception as e:
            self.console.print(f"[red]错误: {e}[/red]")

        self.engine.save_to_history(user_input)

        # 调试：显示记忆管家持久上下文
        if self.debug:
            if self.engine.known_memories:
                mems = "\n".join(f"  [dim]- {m}[/dim]" for m in self.engine.known_memories[-10:])
                self.console.print(f"  [dim yellow on black][DEBUG] 管家上下文 ({len(self.engine.known_memories)}条):[/dim yellow on black]\n{mems}")
            else:
                self.console.print(f"  [dim yellow on black][DEBUG] 管家上下文: (空)[/dim yellow on black]")

        self.console.print()

    def _character_color(self) -> str:
        return "magenta"

    def _print_banner(self):
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
        self.console.print(
            "\n[bold]命令列表:[/bold]\n"
            "  /quit, /exit, /q  — 退出\n"
            "  /clear            — 清除对话历史\n"
            "  /help             — 显示此帮助\n"
        )
