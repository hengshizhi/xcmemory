# -*- coding: utf-8 -*-
"""
星辰记忆 HTTP API 客户端

通过 HTTP 调用星辰记忆数据库的 API 端点：
- POST /api/v1/nl-query — 自然语言查询/写入
- POST /api/v1/query — MQL 精确查询
- GET  /api/v1/systems — 列出系统
- POST /api/v1/systems/{name}/use — 切换系统
- GET  /health — 健康检查
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class NLQueryResult:
    """NL 查询结果"""
    type: str               # "write_only" | "query_only" | "mixed" | "empty"
    response: str           # NL 生成的自然语言回答
    mql: str = ""           # 生成的 MQL 语句
    result_count: int = 0   # 检索结果数量
    results: list = field(default_factory=list)  # 检索到的记忆列表
    writes: int = 0         # 写入的记忆数量


@dataclass
class MQLQueryResult:
    """MQL 查询结果"""
    type: str               # "select" | "insert" | "error"
    data: list = field(default_factory=list)
    affected_rows: int = 0
    message: str = ""


class MemoryClient:
    """星辰记忆 HTTP API 客户端"""

    def __init__(self, base_url: str, api_key: str, system_name: str = "default"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.system_name = system_name
        self._api_prefix = f"{self.base_url}/api/v1"

    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    # ── 健康检查 ──────────────────────────────────────────────

    async def health_check(self) -> bool:
        """检查星辰记忆服务是否在线"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ── 系统管理 ──────────────────────────────────────────────

    async def ensure_system(self) -> bool:
        """确保目标记忆系统存在且已激活，返回是否成功"""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # 1. 列出系统
                resp = await client.get(
                    f"{self._api_prefix}/systems",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    systems = resp.json().get("systems", [])
                    system_names = [s.get("name") for s in systems]

                    # 2. 如果目标系统不存在，创建它
                    if self.system_name not in system_names:
                        create_resp = await client.post(
                            f"{self._api_prefix}/systems",
                            json={"name": self.system_name},
                            headers=self._headers(),
                        )
                        if create_resp.status_code not in (200, 201):
                            return False

                    # 3. 切换到目标系统
                    use_resp = await client.post(
                        f"{self._api_prefix}/systems/{self.system_name}/use",
                        headers=self._headers(),
                    )
                    return use_resp.status_code == 200
                return False
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    # ── NL 查询/写入 ─────────────────────────────────────────

    async def nl_query(self, text: str, top_k: int = 5) -> NLQueryResult:
        """
        自然语言查询/写入（走 NL Pipeline，自动识别写入/查询意图）。

        星辰记忆的 nl-query 端点已内置意图识别：
        - 「回忆一下上次和绯绯的约定」→ 自动识别为查询意图
        - 「记住明天要和绯绯见面」→ 自动识别为写入意图
        - 「我喜欢吃火锅，周末一般干嘛？」→ 混合意图

        Args:
            text: 自然语言文本
            top_k: 检索返回的最大结果数

        Returns:
            NLQueryResult
        """
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._api_prefix}/nl-query",
                    json={"query": text, "top_k": top_k},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

                return NLQueryResult(
                    type=data.get("type", "empty"),
                    response=data.get("response", ""),
                    mql=data.get("mql", ""),
                    result_count=data.get("result_count", 0),
                    results=data.get("results", []),
                    writes=data.get("writes", 0),
                )
        except httpx.HTTPStatusError as e:
            return NLQueryResult(
                type="error",
                response=f"API 错误: {e.response.status_code} - {e.response.text[:200]}",
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return NLQueryResult(
                type="error",
                response=f"连接错误: {type(e).__name__}",
            )

    # ── 记忆系统状态 ─────────────────────────────────────────

    async def count_memories(self) -> int:
        """
        查询当前记忆系统中的记忆总数。

        Returns:
            记忆数量，-1 表示查询失败
        """
        result = await self.mql_query("SELECT * FROM memories LIMIT 1")
        if result.type == "error":
            return -1
        # 尝试获取总行数
        count_result = await self.mql_query(
            "SELECT * FROM memories LIMIT 10000"
        )
        if count_result.type == "error":
            return -1
        return len(count_result.data)

    # ── MQL 精确查询 ─────────────────────────────────────────

    async def mql_query(self, mql: str) -> MQLQueryResult:
        """直接执行 MQL 语句（精确查询）"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._api_prefix}/query",
                    json={"mql": mql},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

                return MQLQueryResult(
                    type=data.get("type", "select"),
                    data=data.get("data", []),
                    affected_rows=data.get("affected_rows", 0),
                    message=data.get("message", ""),
                )
        except httpx.HTTPStatusError as e:
            return MQLQueryResult(
                type="error",
                message=f"API 错误: {e.response.status_code}",
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return MQLQueryResult(
                type="error",
                message=f"连接错误: {type(e).__name__}",
            )
