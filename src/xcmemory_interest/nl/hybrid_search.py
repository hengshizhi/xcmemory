"""
混合检索策略（Hybrid Search）

向量相似度 × 关键词匹配 × 短语精确奖励

公式: final_score = α × vector_sim + β × keyword_score + phrase_bonus

参考: MEMU_TEXT2MEM_REFERENCE.md 第九章
"""

from __future__ import annotations

from typing import Dict, List, Set, TYPE_CHECKING

import jieba

if TYPE_CHECKING:
    from ..pyapi.core import MemorySystem, SearchResult


class HybridSearch:
    """
    混合检索：向量相似度 × 关键词匹配 × 短语精确奖励

    公式: final_score = α × vector_sim + β × keyword_score + phrase_bonus

    Attributes:
        mem: MemorySystem 实例
        alpha: 向量相似度权重，默认 0.7
        beta: 关键词匹配权重，默认 0.3
        phrase_bonus_weight: 短语精确奖励值，默认 0.1
    """

    # 默认停用词列表
    DEFAULT_STOPWORDS: Set[str] = {
        "的", "了", "是", "在", "我", "有", "和", "就",
        "不", "人", "都", "一", "一个", "上", "也", "很",
        "到", "说", "要", "去", "你", "会", "着", "没有",
        "看", "好", "自己", "这", "那", "他", "她", "它",
        "吗", "吧", "呢", "啊", "哦", "嗯", "噢",
    }

    def __init__(
        self,
        memory_system: MemorySystem,
        alpha: float = 0.7,
        beta: float = 0.3,
        phrase_bonus_weight: float = 0.1,
    ):
        """
        初始化混合检索器

        Args:
            memory_system: MemorySystem 实例
            alpha: 向量相似度权重 (0-1)
            beta: 关键词匹配权重 (0-1)
            phrase_bonus_weight: 短语精确奖励值
        """
        self.mem = memory_system
        self.alpha = alpha
        self.beta = beta
        self.phrase_bonus_weight = phrase_bonus_weight

    async def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        执行混合检索

        Args:
            query: 自然语言查询
            top_k: 返回结果数量

        Returns:
            检索结果列表，每项包含 memory_id, score, vector_score, keyword_score, phrase_bonus, memory
        """
        # 1. 向量检索：获取 2*top_k 个候选结果
        search_results = await self._vector_search(query, top_k * 2)

        if not search_results:
            return []

        # 2. 关键词提取
        keywords = self._extract_keywords(query)

        # 3. 批量获取记忆内容
        memory_ids = [r.memory_id for r in search_results]
        memories = self.mem.get_memories(memory_ids)

        # 4. 计算混合分数
        scored_results = []
        for result in search_results:
            memory = memories.get(result.memory_id)
            if not memory:
                continue

            # 向量分数（从 distance 转换，假设归一化向量）
            # L2 distance -> cosine similarity: sim ≈ 1 - distance / 2
            vector_score = max(0.0, 1.0 - result.distance / 2.0)

            # 关键词分数
            keyword_score = self._keyword_score(keywords, memory.content, memory.query_sentence)

            # 短语精确奖励
            phrase_bonus = self._phrase_bonus(query, memory.content)

            # 混合分数
            final_score = (
                self.alpha * vector_score
                + self.beta * keyword_score
                + phrase_bonus * self.phrase_bonus_weight
            )

            scored_results.append({
                "memory_id": result.memory_id,
                "score": final_score,
                "vector_score": vector_score,
                "keyword_score": keyword_score,
                "phrase_bonus": phrase_bonus,
                "memory": memory,
                "distance": result.distance,
                # 扁平化，方便 display 直接读取
                "id": result.memory_id,
                "content": memory.content or "",
                "query_sentence": memory.query_sentence or "",
                "lifecycle": memory.lifecycle if hasattr(memory, "lifecycle") else None,
                "created_at": str(memory.created_at) if hasattr(memory, "created_at") else "",
            })

        # 5. 按 final_score 降序排列
        scored_results.sort(key=lambda x: x["score"], reverse=True)

        return scored_results[:top_k]

    def _extract_keywords(self, query: str) -> Set[str]:
        """
        简单分词 + 停用词过滤

        Args:
            query: 输入查询字符串

        Returns:
            关键词集合
        """
        # 使用 jieba 精确模式分词
        words = jieba.lcut(query, cut_all=False)
        # 过滤停用词和单字
        return {
            w.strip()
            for w in words
            if len(w.strip()) > 1 and w.strip() not in self.DEFAULT_STOPWORDS
        }

    def _keyword_score(
        self, keywords: Set[str], content: str, query_sentence: str
    ) -> float:
        """
        计算关键词匹配分数

        Args:
            keywords: 查询关键词集合
            content: 记忆内容
            query_sentence: 查询句（六槽格式）

        Returns:
            关键词匹配分数 [0, 1]
        """
        if not keywords:
            return 0.0

        # 合并检索文本
        search_text = (content or "") + " " + (query_sentence or "")
        search_text_lower = search_text.lower()

        # 计算匹配的关键词数量
        matched = sum(1 for kw in keywords if kw.lower() in search_text_lower)

        # 归一化分数
        return matched / len(keywords) if keywords else 0.0

    def _phrase_bonus(self, query: str, content: str) -> float:
        """
        短语精确匹配奖励

        Args:
            query: 查询字符串
            content: 记忆内容

        Returns:
            短语奖励值（匹配返回 1.0，否则 0.0）
        """
        if not query or not content:
            return 0.0

        query_lower = query.lower().strip()
        content_lower = content.lower()

        # 检查查询是否作为子串出现在记忆中
        return 1.0 if query_lower in content_lower else 0.0

    async def _vector_search(
        self, query: str, top_k: int
    ) -> List[SearchResult]:
        """
        执行向量检索

        将自然语言查询转换为槽位字典后调用 MemorySystem.search()

        Args:
            query: 自然语言查询
            top_k: 返回数量

        Returns:
            SearchResult 列表
        """
        # 将查询解析为槽位字典
        # 简单策略：提取查询中的关键信息作为 subject
        slot_dict = self._parse_query_to_slots(query)

        # 调用 MemorySystem 的搜索接口
        results = self.mem.search(query_slots=slot_dict, top_k=top_k, use_subspace=True)

        return results

    def _parse_query_to_slots(self, query: str) -> Dict[str, str]:
        """
        将自然语言查询解析为槽位字典

        简化实现：尝试识别主体槽位

        Args:
            query: 自然语言查询

        Returns:
            槽位字典 {"subject": "...", ...}
        """
        # 简化实现：直接使用查询作为 subject 进行搜索
        # 更复杂的实现可以调用 SlotExtractor 进行完整解析
        keywords = self._extract_keywords(query)

        if keywords:
            # 取第一个关键词作为主体
            return {"subject": list(keywords)[0]}

        # 如果没有关键词，返回空字典让 MemorySystem 使用全空间搜索
        return {}
