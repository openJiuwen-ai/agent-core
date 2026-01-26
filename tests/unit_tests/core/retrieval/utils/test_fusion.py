# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Fusion utility function test cases
"""


from openjiuwen.core.retrieval import rrf_fusion
from openjiuwen.core.retrieval import RetrievalResult, SearchResult


class TestRRFFusion:
    """RRF fusion tests"""

    @staticmethod
    def test_rrf_fusion_single_list():
        """测试单个结果列表融合"""
        results = [
            RetrievalResult(text="Result 1", score=0.9),
            RetrievalResult(text="Result 2", score=0.8),
            RetrievalResult(text="Result 3", score=0.7),
        ]
        fused = rrf_fusion([results])
        assert len(fused) == 3
        # Should be sorted by RRF score
        assert fused[0].text == "Result 1"
        assert fused[0].score > fused[1].score

    @staticmethod
    def test_rrf_fusion_multiple_lists():
        """测试多个结果列表融合"""
        results1 = [
            RetrievalResult(text="Result 1", score=0.9),
            RetrievalResult(text="Result 2", score=0.8),
        ]
        results2 = [
            RetrievalResult(text="Result 2", score=0.85),
            RetrievalResult(text="Result 3", score=0.7),
        ]
        fused = rrf_fusion([results1, results2])
        # Should deduplicate and sort by RRF score
        assert len(fused) == 3
        texts = [r.text for r in fused]
        assert "Result 1" in texts
        assert "Result 2" in texts
        assert "Result 3" in texts
        # Result 2 should rank higher (appears in both lists)
        assert fused[0].text == "Result 2" or fused[1].text == "Result 2"

    @staticmethod
    def test_rrf_fusion_empty_list():
        """测试空列表融合"""
        fused = rrf_fusion([])
        assert len(fused) == 0

    @staticmethod
    def test_rrf_fusion_with_empty_results():
        """测试包含空结果的融合"""
        results1 = [
            RetrievalResult(text="Result 1", score=0.9),
        ]
        results2 = []
        fused = rrf_fusion([results1, results2])
        assert len(fused) == 1
        assert fused[0].text == "Result 1"

    @staticmethod
    def test_rrf_fusion_custom_k():
        """测试自定义 k 参数"""
        results1 = [
            RetrievalResult(text="Result 1", score=0.9),
            RetrievalResult(text="Result 2", score=0.8),
        ]
        results2 = [
            RetrievalResult(text="Result 2", score=0.85),
            RetrievalResult(text="Result 3", score=0.7),
        ]
        fused_k30 = rrf_fusion([results1, results2], k=30)
        fused_k60 = rrf_fusion([results1, results2], k=60)
        # Different k values should result in different RRF scores
        assert len(fused_k30) == len(fused_k60) == 3

    @staticmethod
    def test_rrf_fusion_with_search_result():
        """测试使用 SearchResult 的融合"""
        results = [
            SearchResult(id="1", text="Result 1", score=0.9),
            SearchResult(id="2", text="Result 2", score=0.8),
        ]
        fused = rrf_fusion([results])
        assert len(fused) == 2
        assert isinstance(fused[0], SearchResult)
