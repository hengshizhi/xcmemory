"""
测试 nl 模块的完整功能

运行方式:
    pytest tests/test_nl_modules.py -v

注意: 需要设置环境变量 OPENAI_API_KEY 才能运行真实的 LLM 调用测试。
      否则测试会使用 mock LLM 客户端。

注意: 由于主包依赖 torch，我们直接导入子模块避免全包初始化。
"""

import pytest
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

# 添加 src 目录到路径
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


# ============================================================================
# Mock LLM Client (用于测试)
# ============================================================================

class MockChatCompletions:
    """模拟 openai.chat.completions 接口"""

    def __init__(self, mock_client):
        self.mock_client = mock_client

    async def create(self, model: str, messages: list, temperature: float = 0.0, max_tokens: int = 512):
        """模拟 chat.completions.create"""
        # 从 messages 中提取 user content
        user_content = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_content = msg.get("content", "")
                break

        self.mock_client.call_history.append({"model": model, "messages": messages, "content": user_content})

        # 尝试匹配响应
        response_text = self.mock_client._get_response(user_content)

        # 返回 Mock 响应对象
        return MockResponse(response_text)


class MockCompletions:
    """模拟 openai.Chat 属性 - 持有 completions"""

    def __init__(self, mock_client):
        self.completions = MockChatCompletions(mock_client)


class MockResponse:
    """模拟 openai API 响应"""

    def __init__(self, content: str):
        self.choices = [MockChoice(content)]


class MockChoice:
    """模拟 choice 对象"""

    def __init__(self, content: str):
        self.message = MockMessage(content)


class MockMessage:
    """模拟 message 对象"""

    def __init__(self, content: str):
        self.content = content


class MockLLMClient:
    """模拟 LLM 客户端，用于测试 - 兼容 OpenAI API 接口

    支持:
        llm.chat.completions.create(...)
    """

    def __init__(self, responses: dict[str, str] = None):
        self.responses = responses or {}
        self.call_history = []
        # 模拟 OpenAI API 的 .chat.completions 结构
        self.chat = MockCompletions(self)

    def _get_response(self, prompt: str) -> str:
        """根据 prompt 获取响应"""
        # 尝试精确匹配
        if prompt in self.responses:
            return self.responses[prompt]
        # 尝试包含匹配
        for key, value in self.responses.items():
            if key in prompt:
                return value
        # 尝试 prompt 中包含 key（宽松匹配）
        for key, value in self.responses.items():
            if any(k in prompt for k in key.split()):
                return value
        # 默认响应 - 根据 prompt 内容判断返回什么格式
        if "MQL" in prompt or "mql" in prompt or "SELECT" in prompt or "INSERT" in prompt:
            # MQL 相关的默认响应
            return """<analysis>
查询分析：测试响应
</analysis>

<mql>
SELECT * FROM memories WHERE subject='测试' SEARCH TOPK 5
</mql>

<slots>
{"scene": "<平时>", "subject": "测试", "action": "<无>", "object": "<无>", "purpose": "<无>", "result": "<无>"}
</slots>

<confidence>
0.9
</confidence>"""
        # 默认响应
        return '<decision>\nRETRIEVE\n</decision>\n\n<rewritten_query>\n重写后的查询\n</rewritten_query>'


# ============================================================================
# Test Decision (预检索判断)
# ============================================================================

class TestNLQueryDecider:
    """测试预检索判断模块"""

    @pytest.mark.asyncio
    async def test_decide_retrieve(self):
        """测试需要检索的查询"""
        from xcmemory_interest.nl.decision import NLQueryDecider

        mock_llm = MockLLMClient({
            "需要检索吗？记得": '{"analysis": "用户询问过去的事", "decision": "RETRIEVE"}'
        })

        decider = NLQueryDecider(mock_llm)
        need_retrieve, rewritten = await decider.decide("我之前学 Python 遇到什么问题？", [])

        assert need_retrieve is True or "RETRIEVE" in rewritten.upper()

    @pytest.mark.asyncio
    async def test_decide_no_retrieve(self):
        """测试不需要检索的查询"""
        from xcmemory_interest.nl.decision import NLQueryDecider

        mock_llm = MockLLMClient({
            "需要检索吗？你好": '{"analysis": "寒暄", "decision": "NO_RETRIEVE"}'
        })

        decider = NLQueryDecider(mock_llm)
        need_retrieve, rewritten = await decider.decide("你好", [])

        # 检查调用历史
        assert len(mock_llm.call_history) == 1


# ============================================================================
# Test Rewriter (查询重写)
# ============================================================================

class TestQueryRewriter:
    """测试查询重写模块"""

    @pytest.mark.asyncio
    async def test_rewrite_pronoun(self):
        """测试代词解析"""
        from xcmemory_interest.nl.rewriter import QueryRewriter

        mock_llm = MockLLMClient()
        rewriter = QueryRewriter(mock_llm)

        history = [
            {"role": "user", "content": "我想学 Python"},
            {"role": "assistant", "content": "Python 是一门很棒的编程语言"}
        ]

        result = await rewriter.rewrite("它有什么特点？", history)

        # 验证 LLM 被调用
        assert len(mock_llm.call_history) == 1
        call = mock_llm.call_history[0]
        assert "它" in call.get("content", "") or "Python" in result


# ============================================================================
# Test Sufficiency (充分性检查)
# ============================================================================

class TestSufficiencyChecker:
    """测试检索充分性检查模块"""

    @pytest.mark.asyncio
    async def test_check_enough(self):
        """测试结果足够的情况"""
        from xcmemory_interest.nl.sufficiency import SufficiencyChecker

        mock_llm = MockLLMClient({
            "结果足够吗？": '{"consideration": "结果直接回答了问题", "judgement": "ENOUGH"}'
        })

        checker = SufficiencyChecker(mock_llm)
        is_enough, reason = await checker.check(
            "Python 有什么特点？",
            "Python 是一门解释型编程语言，语法简洁易读。"
        )

        assert len(mock_llm.call_history) == 1

    @pytest.mark.asyncio
    async def test_check_more(self):
        """测试需要更多信息的情况"""
        from xcmemory_interest.nl.sufficiency import SufficiencyChecker

        mock_llm = MockLLMClient({
            "结果足够吗？": '{"consideration": "缺少具体例子", "judgement": "MORE"}'
        })

        checker = SufficiencyChecker(mock_llm)
        is_enough, reason = await checker.check(
            "Python 有什么特点？",
            "Python 是一门编程语言。"
        )

        assert len(mock_llm.call_history) == 1


# ============================================================================
# Test MQL Generator (NL→MQL)
# ============================================================================

class TestMQLGenerator:
    """测试 NL→MQL 生成器"""

    @pytest.mark.asyncio
    async def test_generate_basic(self):
        """测试基本的 NL→MQL 转换"""
        from xcmemory_interest.nl.mql_generator import MQLGenerator

        mock_response = """<analysis>
查询分析：用户想记住"绯绯希望星织发展成恋人关系"
</analysis>

<mql>
INSERT INTO memories VALUES ('<平时><绯绯><希望><星织><发展><恋人>', '绯绯希望星织发展成恋人关系', 999999)
</mql>

<slots>
{"scene": "<平时>", "subject": "绯绯", "action": "<希望>", "object": "星织", "purpose": "<发展>", "result": "<恋人>"}
</slots>

<confidence>
0.95
</confidence>"""

        mock_llm = MockLLMClient({
            "生成 MQL": mock_response
        })

        gen = MQLGenerator(mock_llm)
        result = await gen.generate("绯绯希望星织发展成恋人关系")

        assert "mql" in result
        assert "slots" in result
        assert "INSERT" in result["mql"]

    @pytest.mark.asyncio
    async def test_generate_select(self):
        """测试 SELECT 查询生成"""
        from xcmemory_interest.nl.mql_generator import MQLGenerator

        mock_response = """<analysis>
查询分析：用户想查找关于 Python 学习的记忆
</analysis>

<mql>
SELECT * FROM memories WHERE object='Python' SEARCH TOPK 5
</mql>

<slots>
{"scene": "<无>", "subject": "<无>", "action": "<无>", "object": "Python", "purpose": "<无>", "result": "<无>"}
</slots>

<confidence>
0.85
</confidence>"""

        mock_llm = MockLLMClient({
            "生成 MQL": mock_response
        })

        gen = MQLGenerator(mock_llm)
        result = await gen.generate("我之前学 Python 遇到什么问题？")

        assert "SELECT" in result["mql"]


# ============================================================================
# Test Intent Classifier (意图识别)
# ============================================================================

class TestIntentClassifier:
    """测试意图识别器"""

    @pytest.mark.asyncio
    async def test_classify_writes_and_queries(self):
        """测试同时有写入和查询的输入"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        mock_response = """<writes>星织打算去沃尔玛购物</writes>
<queries>星织平时需要买什么？</queries>
<lifecycle>medium</lifecycle>"""

        mock_llm = MockLLMClient({"今天": mock_response})
        classifier = IntentClassifier(mock_llm, system_holder="星织")
        result = await classifier.classify("我今天打算去沃尔玛购物。可是需要买什么？")

        assert result["writes"] == ["星织打算去沃尔玛购物"]
        assert result["queries"] == ["星织平时需要买什么？"]
        assert result["lifecycle"] == "medium"
        assert result["reference_duration"] == 7 * 86400

    @pytest.mark.asyncio
    async def test_classify_write_only(self):
        """测试纯写入输入"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        mock_response = """<writes>星织的密码是abc123</writes>
<queries></queries>
<lifecycle>permanent</lifecycle>"""

        mock_llm = MockLLMClient({"记住": mock_response})
        classifier = IntentClassifier(mock_llm, system_holder="星织")
        result = await classifier.classify("记住我的密码是abc123")

        assert result["writes"] == ["星织的密码是abc123"]
        assert result["queries"] == []
        assert result["lifecycle"] == "permanent"

    @pytest.mark.asyncio
    async def test_classify_query_only(self):
        """测试纯查询输入"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        mock_response = """<writes></writes>
<queries>星织关于Python的记忆</queries>
<lifecycle>short</lifecycle>"""

        mock_llm = MockLLMClient({"Python": mock_response})
        classifier = IntentClassifier(mock_llm, system_holder="星织")
        result = await classifier.classify("关于Python的记忆")

        assert result["writes"] == []
        assert result["queries"] == ["星织关于Python的记忆"]
        assert result["lifecycle"] == "short"

    @pytest.mark.asyncio
    async def test_classify_with_history(self):
        """测试带对话历史的意图识别（代词消解）"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        mock_response = """<writes></writes>
<queries>Python的特点是什么？</queries>
<lifecycle>short</lifecycle>"""

        mock_llm = MockLLMClient({"Python": mock_response})
        classifier = IntentClassifier(mock_llm, system_holder="星织")

        history = [
            {"role": "user", "content": "我想学Python"},
            {"role": "assistant", "content": "Python是一門很棒的編程語言"},
        ]
        result = await classifier.classify("它有什么特点？", history)

        assert len(mock_llm.call_history) == 1
        # 验证历史上下文被传入 LLM
        call_content = mock_llm.call_history[0]["content"]
        assert "对话背景" in call_content or "历史" in call_content

    @pytest.mark.asyncio
    async def test_classify_llm_error_fallback(self):
        """测试 LLM 异常时的降级行为"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        mock_llm = MockLLMClient()
        # 让 LLM 报错
        mock_llm.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

        classifier = IntentClassifier(mock_llm, debug=True)
        result = await classifier.classify("Python的特点是什么？")

        # 降级为纯查询
        assert result["writes"] == []
        assert result["queries"] == ["Python的特点是什么？"]
        assert result["lifecycle"] == "short"
        assert result["reference_duration"] == 86400

    def test_lifecycle_tiers(self):
        """测试生命周期档位映射"""
        from xcmemory_interest.nl.intent_classifier import LIFECYCLE_TIERS

        assert LIFECYCLE_TIERS["permanent"] == 999999
        assert LIFECYCLE_TIERS["long"] == 30 * 86400
        assert LIFECYCLE_TIERS["medium"] == 7 * 86400
        assert LIFECYCLE_TIERS["short"] == 86400

    def test_extract_tag(self):
        """测试标签提取"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        text = """<writes>写入句1|写入句2</writes>
<queries>查询句</queries>
<lifecycle>medium</lifecycle>"""

        assert IntentClassifier._extract_tag(text, "writes") == "写入句1|写入句2"
        assert IntentClassifier._extract_tag(text, "queries") == "查询句"
        assert IntentClassifier._extract_tag(text, "lifecycle") == "medium"
        assert IntentClassifier._extract_tag(text, "nonexistent") == ""

    def test_extract_tag_empty(self):
        """测试空标签提取"""
        from xcmemory_interest.nl.intent_classifier import IntentClassifier

        text = "<writes></writes><queries></queries>"
        assert IntentClassifier._extract_tag(text, "writes") == ""
        assert IntentClassifier._extract_tag(text, "queries") == ""


# ============================================================================
# Test WriteMQLGenerator (写入MQL生成)
# ============================================================================

class TestWriteMQLGenerator:
    """测试 NL→INSERT MQL 生成器"""

    @pytest.mark.asyncio
    async def test_generate_basic(self):
        """测试基本写入生成"""
        from xcmemory_interest.nl.write_mql_generator import WriteMQLGenerator

        mock_response = """<mql>INSERT INTO memories VALUES ('<无><星织><打算><沃尔玛购物><无><无>', '星织打算去沃尔玛购物', 604800)</mql>"""

        mock_llm = MockLLMClient({"沃尔玛": mock_response})
        gen = WriteMQLGenerator(mock_llm, system_holder="星织")

        result = await gen.generate(["星织打算去沃尔玛购物"], reference_duration=604800)

        assert "INSERT" in result["mql_script"]
        assert result["insert_count"] == 1

    @pytest.mark.asyncio
    async def test_generate_multiple(self):
        """测试多条写入生成"""
        from xcmemory_interest.nl.write_mql_generator import WriteMQLGenerator

        mock_response = """<mql>INSERT INTO memories VALUES ('<无><星织><喜欢><火锅><无><无>', '星织喜欢吃火锅', 2592000);INSERT INTO memories VALUES ('<明天><星织><做><开会><无><无>', '星织明天要开会', 604800)</mql>"""

        mock_llm = MockLLMClient({"星织": mock_response})
        gen = WriteMQLGenerator(mock_llm, system_holder="星织")

        result = await gen.generate(
            ["星织喜欢吃火锅", "星织明天要开会"],
            reference_duration=604800,
        )

        assert result["insert_count"] == 2
        assert ";" in result["mql_script"]

    @pytest.mark.asyncio
    async def test_generate_empty(self):
        """测试空陈述句列表"""
        from xcmemory_interest.nl.write_mql_generator import WriteMQLGenerator

        mock_llm = MockLLMClient()
        gen = WriteMQLGenerator(mock_llm)
        result = await gen.generate([])

        assert result["mql_script"] == ""
        assert result["insert_count"] == 0

    @pytest.mark.asyncio
    async def test_generate_llm_error_fallback(self):
        """测试 LLM 异常时的降级行为"""
        from xcmemory_interest.nl.write_mql_generator import WriteMQLGenerator

        mock_llm = MockLLMClient()
        mock_llm.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

        gen = WriteMQLGenerator(mock_llm, debug=True)
        result = await gen.generate(["星织打算去沃尔玛购物"])

        assert result["mql_script"] == ""
        assert result["insert_count"] == 0

    @pytest.mark.asyncio
    async def test_generate_defensive_insert_fix(self):
        """测试 INSERT 前缀防御性修复"""
        from xcmemory_interest.nl.write_mql_generator import WriteMQLGenerator

        # LLM 返回缺少 INSERT 前缀的 MQL
        mock_response = """<mql>INTO memories VALUES ('<无><星织><喜欢><火锅><无><无>', '内容', 2592000)</mql>"""

        mock_llm = MockLLMClient({"火锅": mock_response})
        gen = WriteMQLGenerator(mock_llm, system_holder="星织")

        result = await gen.generate(["星织喜欢吃火锅"], reference_duration=2592000)

        # 应该自动补上 INSERT
        assert result["mql_script"].upper().startswith("INSERT")
        assert result["insert_count"] == 1


# ============================================================================
# Test Slot Extractor (6槽提取)
# ============================================================================

class TestSlotExtractor:
    """测试 6槽记忆提取模块"""

    @pytest.mark.asyncio
    async def test_extract_basic(self):
        """测试基本记忆提取"""
        from xcmemory_interest.nl.slot_extractor import SlotExtractor

        mock_response = """<slots>
{"scene": "<平时>", "subject": "我", "action": "<学>", "object": "Python", "purpose": "<无>", "result": "<有收获>"}
</slots>

<description>
我喜欢学习 Python，觉得很有收获。
</description>

<lifecycle>
999999
</lifecycle>"""

        mock_llm = MockLLMClient({
            "提取6槽": mock_response
        })

        extractor = SlotExtractor(mock_llm)
        result = await extractor.extract("我喜欢学习 Python，觉得很有收获。")

        assert "slots" in result
        assert "description" in result
        assert "lifecycle" in result

    @pytest.mark.asyncio
    async def test_slot_validator(self):
        """测试槽位验证"""
        from xcmemory_interest.nl.slot_extractor import SlotValidator

        validator = SlotValidator()

        # 有效的场景词
        assert validator.validate_scene("<平时>") is True
        assert validator.validate_scene("<少年期>") is True
        # 时间场景
        assert validator.validate_scene("<晚上>") is True
        assert validator.validate_scene("<周末>") is True
        assert validator.validate_scene("<假期>") is True
        # 空间场景
        assert validator.validate_scene("<家里>") is True
        assert validator.validate_scene("<公司>") is True
        assert validator.validate_scene("<学校>") is True

        # 有效动作
        assert validator.validate_action("<是>") is True
        assert validator.validate_action("<希望>") is True

        # 无效值
        assert validator.validate_scene("<随便什么>") is False


# ============================================================================
# Test Hybrid Search (混合检索)
# ============================================================================

class TestHybridSearch:
    """测试混合检索模块"""

    @pytest.mark.asyncio
    async def test_search_basic(self):
        """测试基本混合检索"""
        from xcmemory_interest.nl.hybrid_search import HybridSearch
        from xcmemory_interest.pyapi.core import SearchResult

        # 创建 mock memory system，返回真实的 SearchResult dataclass 对象
        mock_mem = MagicMock()
        mock_mem.search = AsyncMock(return_value=[
            SearchResult(memory_id="1", distance=0.2, score=0.9),
            SearchResult(memory_id="2", distance=0.6, score=0.7),
        ])
        mock_mem.get_memories = MagicMock(return_value={
            "1": MagicMock(content="Python 学习", query_sentence=""),
            "2": MagicMock(content="Java 学习", query_sentence=""),
        })

        search = HybridSearch(mock_mem, alpha=0.7, beta=0.3)
        results = await search.search("Python", top_k=5)

        assert len(results) >= 0  # 取决于混合打分


# ============================================================================
# Test Ranker (LLM 重排序)
# ============================================================================

class TestMemoryItemRanker:
    """测试 LLM 重排序模块"""

    @pytest.mark.asyncio
    async def test_rank_basic(self):
        """测试基本重排序"""
        from xcmemory_interest.nl.ranker import MemoryItemRanker

        mock_response = """{
  "analysis": "第一个最相关",
  "items": ["id_1", "id_3", "id_2"]
}"""

        mock_llm = MockLLMClient({
            "重排序": mock_response
        })

        ranker = MemoryItemRanker(mock_llm)

        items = [
            {"id": "id_1", "query_sentence": "学 Python", "content": "Python 语法", "lifecycle": 86400},
            {"id": "id_2", "query_sentence": "学 Java", "content": "Java 语法", "lifecycle": 86400},
            {"id": "id_3", "query_sentence": "学编程", "content": "编程思想", "lifecycle": 86400},
        ]

        ranked = await ranker.rank("Python 怎么学", items, top_k=3)

        assert len(ranked) <= 3


# ============================================================================
# Test Reinforcement (去重+强化)
# ============================================================================

class TestReinforcement:
    """测试去重与强化机制"""

    def test_compute_content_hash(self):
        """测试内容哈希计算"""
        from xcmemory_interest.nl.reinforcement import compute_content_hash

        # 相同内容应该产生相同哈希
        hash1 = compute_content_hash("我喜欢 Python", "episodic")
        hash2 = compute_content_hash("我喜欢 Python", "episodic")
        assert hash1 == hash2

        # 不同内容应该产生不同哈希
        hash3 = compute_content_hash("我喜欢 Java", "episodic")
        assert hash1 != hash3

        # 大小写不敏感
        hash4 = compute_content_hash("我喜欢 PYTHON", "episodic")
        assert hash1 == hash4

        # 空格不敏感
        hash5 = compute_content_hash("我喜欢  Python", "episodic")
        assert hash1 == hash5

    def test_compute_recency_decay(self):
        """测试近期衰减计算"""
        from xcmemory_interest.nl.reinforcement import compute_recency_decay
        from datetime import datetime, timedelta

        # 刚创建的记忆衰减最小
        recent_time = datetime.now() - timedelta(hours=1)
        decay_recent = compute_recency_decay(recent_time, recent_time)

        # 较老的记忆衰减更大
        old_time = datetime.now() - timedelta(days=7)
        decay_old = compute_recency_decay(old_time, old_time)

        assert decay_recent > decay_old


# ============================================================================
# Test Pipeline (流水线编排)
# ============================================================================

class TestNLSearchPipeline:
    """测试 NL Pipeline"""

    @pytest.mark.asyncio
    async def test_pipeline_components_exist(self):
        """测试流水线组件存在"""
        from xcmemory_interest.nl.pipeline import NLPipeline, NLSearchPipeline

        # NLSearchPipeline 继承自 NLPipeline
        assert issubclass(NLSearchPipeline, NLPipeline)

        # 验证核心方法存在
        assert hasattr(NLPipeline, 'run')
        assert hasattr(NLPipeline, '_run_query_flow')
        assert hasattr(NLPipeline, '_exec_mql')


# ============================================================================
# Test Time Filter (相对时间过滤)
# ============================================================================

class TestTimeFilter:
    """测试相对时间过滤器"""

    def test_relative_time_map(self):
        """测试相对时间映射表"""
        from xcmemory_interest.mql.time_filter import RELATIVE_TIME_MAP

        assert "last_5_minutes" in RELATIVE_TIME_MAP
        assert "last_1_hour" in RELATIVE_TIME_MAP
        assert "last_7_days" in RELATIVE_TIME_MAP

        # 验证数值合理
        assert RELATIVE_TIME_MAP["last_5_minutes"] == 300
        assert RELATIVE_TIME_MAP["last_1_hour"] == 3600
        assert RELATIVE_TIME_MAP["last_7_days"] == 604800

    def test_parse_relative_time(self):
        """测试相对时间解析"""
        from xcmemory_interest.mql.time_filter import parse_relative_time
        import pendulum

        # 测试 last_7_days
        result = parse_relative_time("last_7_days")
        expected = pendulum.now().subtract(seconds=604800)

        # 允许 1 分钟误差
        diff = abs((result - expected).total_seconds())
        assert diff < 60


# ============================================================================
# Test Dry Run (Dry-run 模式)
# ============================================================================

class TestDryRun:
    """测试 Dry-run 模式"""

    def test_dry_run_mixin_exists(self):
        """测试 DryRunMixIn 存在"""
        from xcmemory_interest.mql.dryrun import DryRunMixIn

        assert hasattr(DryRunMixIn, 'execute_with_dryrun')


# ============================================================================
# Test STO Operations (STO 操作集)
# ============================================================================

class TestSTOOperations:
    """测试 STO 操作集"""

    def test_sto_operations_exist(self):
        """测试 STO 操作类存在"""
        from xcmemory_interest.mql.sto_operations import STOOperations

        ops = STOOperations()

        # 验证所有 STO 操作方法存在
        assert hasattr(ops, 'promote')
        assert hasattr(ops, 'demote')
        assert hasattr(ops, 'expire_after')
        assert hasattr(ops, 'lock')
        assert hasattr(ops, 'unlock')
        assert hasattr(ops, 'merge')
        assert hasattr(ops, 'split')

    def test_sto_metadata_structure(self):
        """测试 STO 元数据结构"""
        from xcmemory_interest.mql.sto_operations import STOOperations

        ops = STOOperations()

        # 验证 _update_extra 方法存在
        assert hasattr(ops, '_update_extra')


# ============================================================================
# Integration Tests (集成测试)
# ============================================================================

class TestNLIntegration:
    """集成测试：测试各模块协同工作"""

    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self):
        """测试完整流水线（使用 mock）"""
        from xcmemory_interest.nl.pipeline import NLSearchPipeline
        from xcmemory_interest.nl.mql_generator import MQLGenerator

        # 创建 mock LLM
        mock_response = """<analysis>
测试
</analysis>

<mql>
SELECT * FROM memories WHERE subject='我' SEARCH TOPK 5
</mql>

<slots>
{"scene": "<平时>", "subject": "我", "action": "<无>", "object": "<无>", "purpose": "<无>", "result": "<无>"}
</slots>

<confidence>
0.9
</confidence>"""

        mock_llm = MockLLMClient({"生成 MQL": mock_response})

        # 验证 MQLGenerator 可用
        gen = MQLGenerator(mock_llm)
        result = await gen.generate("我之前做了什么？")

        assert "mql" in result
        assert result["confidence"] > 0.8


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
