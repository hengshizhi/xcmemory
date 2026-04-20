# -*- coding: utf-8 -*-
"""
NL 模块 API 使用示例

展示两种调用方式：
1. HTTP API（推荐，生产环境使用）
2. 直接调用 Pipeline（开发/测试用）

运行前确保服务已启动：
    .\\venv\\Scripts\\python.exe start_api_only.py  # HTTP: localhost:8080, WebSocket: localhost:8081
"""

import asyncio
import httpx

BASE_URL = "http://localhost:8080/api/v1"
API_KEY = "xi-admin-i60v1a2Ytrqa4GWh6JSvwg7WZOjzxJ0lBGZFDHoY-LI"

# =============================================================================
# 方式一：HTTP API（生产推荐）
# =============================================================================


async def http_nl_query(query: str, top_k: int = 10):
    """
    调用 POST /api/v1/nl-query

    注意：HTTP API 暂不支持 history 传参（已在服务端硬编码为空列表）。

    Args:
        query: 自然语言查询
        top_k: 检索返回的最大结果数，默认 10

    Returns:
        dict，包含字段：
        - type: "direct" 或 "retrieved"
        - query: 原始查询
        - response: LLM 生成的自然语言回答
        - mql: 生成的 MQL 语句
        - slots: 槽位提取结果
        - result_count: 结果数量
        - results: 检索结果列表
    """
    payload = {"query": query, "top_k": top_k}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BASE_URL}/nl-query",
            json=payload,
            headers={"X-Api-Key": API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def http_mql_query(mql: str):
    """
    调用 POST /api/v1/query，执行原生 MQL 语句

    Args:
        mql: MQL 查询语句，如 "SELECT * WHERE interest ~= 'python'"

    Returns:
        dict，包含 result 列表
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BASE_URL}/query",
            json={"mql": mql},
            headers={"X-Api-Key": API_KEY},
        )
        resp.raise_for_status()
        return resp.json()

async def main():
    print("=" * 60)
    print("NL 模块 API 使用示例")
    print("=" * 60)

    print("\n--- 1. HTTP: nl-query（自然语言查询）---")
    result = await http_nl_query("我最近记了什么？")
    print(f"type:        {result.get('type')}")
    print(f"mql:         {result.get('mql')}")
    print(f"response:    {str(result.get('response', ''))[:80]}...")
    print(f"result_count: {result.get('result_count')}")

    print("\n--- 2. HTTP: nl-query（指定 top_k）---")
    result = await http_nl_query("查找关于Python的记忆", top_k=3)
    print(f"type:         {result.get('type')}")
    print(f"mql:          {result.get('mql')}")
    print(f"result_count: {result.get('result_count')}")
    if result.get("results"):
        for i, r in enumerate(result["results"], 1):
            print(f"  [{i}] {str(r.get('content', ''))[:60]}...")

    print("\n--- 3. HTTP: query（原生 MQL）---")
    result = await http_mql_query("SELECT * WHERE interest ~= 'python' LIMIT 3")
    print(f"MQL执行成功，结果数: {len(result.get('data', []))}")


if __name__ == "__main__":
    asyncio.run(main())
