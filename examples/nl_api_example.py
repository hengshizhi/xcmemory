# -*- coding: utf-8 -*-
"""
NL 模块 API 使用示例

展示两种调用方式：
1. HTTP API（推荐，生产环境使用）
2. 直接调用 Pipeline（开发/测试用）

运行前确保服务已启动：
    .\venv\Scripts\python.exe start_api_only.py
"""

import asyncio
import httpx

BASE_URL = "http://localhost:8000/api/v1"

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

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{BASE_URL}/nl-query", json=payload)
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
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{BASE_URL}/query", json={"mql": mql})
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# 方式二：直接调用 Pipeline（开发/测试用）
# =============================================================================


async def direct_pipeline_example():
    """
    直接 import nl 模块，组合使用各组件
    """
    import sys
    sys.path.insert(0, "src")

    from openai import AsyncOpenAI
    from xcmemory_interest.nl import NLSearchPipeline
    from xcmemory_interest.pyapi.core import PyAPI

    # 1. 初始化 LLM 客户端（OpenRouter）
    llm = AsyncOpenAI(
        api_key="your-openrouter-api-key",
        base_url="https://openrouter.ai/api/v1",
    )

    # 2. 初始化 MemorySystem
    mem = PyAPI(".")  # 数据库根目录
    active = mem.active_system

    # 3. 创建 NL Pipeline
    pipeline = NLSearchPipeline(
        llm_client=llm,
        memory_system=active,
        model="xiaomi/mimo-v2-flash",
        debug=True,
    )

    # 4. 执行查询
    result = await pipeline.run(
        nl_query="我最近关于 Python 的记忆有哪些？",
        history=[{"role": "user", "content": "Python 是什么？"}],
        top_k=5,
    )
    print(result)


# =============================================================================
# 演示：各组件独立使用
# =============================================================================


async def component_examples():
    """演示 NL 模块各组件的独立用法"""
    import sys
    sys.path.insert(0, "src")

    from openai import AsyncOpenAI
    from xcmemory_interest.nl import (
        NLQueryDecider,
        QueryRewriter,
        MQLGenerator,
        SufficiencyChecker,
        SlotExtractor,
        MemoryItemRanker,
    )

    llm = AsyncOpenAI(
        api_key="your-openrouter-api-key",
        base_url="https://openrouter.ai/api/v1",
    )
    model = "xiaomi/mimo-v2-flash"

    # --- 1. 预检索判断：判断是否需要检索 ---
    decider = NLQueryDecider(llm, model=model)
    need_retrieve, direct_response = await decider.decide(
        "今天天气怎么样？", []
    )
    print(f"需要检索: {need_retrieve}, 直接回复: {direct_response}")

    # --- 2. 查询重写：解析代词/引用 ---
    rewriter = QueryRewriter(llm, model=model)
    history = [{"role": "user", "content": "我上周写的Python代码"}]
    rewritten = await rewriter.rewrite("那个项目怎么样了？", history)
    print(f"重写后: {rewritten}")

    # --- 3. NL → MQL 生成 ---
    gen = MQLGenerator(llm, model=model)
    mql_plan = await gen.generate_with_fallback("查找关于Python的最近记忆")
    print(f"MQL: {mql_plan['mql']}, 置信度: {mql_plan.get('confidence')}, 降级: {mql_plan.get('fallback')}")

    # --- 4. 充分性检查：判断结果是否足够 ---
    checker = SufficiencyChecker(llm, model=model)
    is_enough, suggestion = await checker.check(
        "Python的特点",
        "Python是一门高级编程语言，语法简洁，适合快速开发。"
    )
    print(f"足够: {is_enough}, 建议: {suggestion}")

    # --- 5. 槽位提取：提取6槽记忆 ---
    extractor = SlotExtractor(llm, model=model)
    raw_text = "上周三在GitHub上看到的一个Rust项目很不错"
    slots = await extractor.extract(raw_text)
    print(f"槽位: {slots}")

    # 槽位验证
    validator = SlotValidator()
    valid, errors = validator.validate(slots)
    print(f"验证通过: {valid}, 错误: {errors}")

    # --- 6. 记忆重排 ---
    ranker = MemoryItemRanker(llm, model=model)
    items = [
        {"content": "Python语法简洁", "query_sentence": "兴趣"},
        {"content": "Rust性能优秀", "query_sentence": "兴趣"},
    ]
    reranked = await ranker.rank("我对什么技术感兴趣？", items)
    print(f"重排结果: {reranked}")


# =============================================================================
# 主函数：演示 HTTP API 调用
# =============================================================================


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
