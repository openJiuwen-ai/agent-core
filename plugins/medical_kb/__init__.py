"""
Medical Knowledge Base Adapter for openJiuwen DeepSearch

将 medical-ai-innovation 的知识库RAG能力适配到 DeepSearch 框架，
作为医疗领域知识库插件，提供诊疗指南的智能检索与问答。

适配器说明:
- 实现 DeepSearch 的 KnowledgeBasePlugin 接口
- 支持多级检索（向量+关键词+知识图谱）
- 支持引用溯源（片段级引用）
"""
from __future__ import annotations


try:
    from medical_ai_innovation.knowledge_base.schemas import (
        Guideline, RetrievalResult, AnswerWithCitations
    )
    from medical_ai_innovation.knowledge_base.kb_engine import KnowledgeBaseEngine
    from medical_ai_innovation.knowledge_base.rag_pipeline import RAGPipeline
except ImportError as exc:
    raise ImportError(
        "无法导入 medical_ai_innovation。请二选一："
        "1) 先执行 `pip install medical-ai-innovation`；"
        "2) 开发模式：将包含 medical_ai_innovation 包的目录加入 PYTHONPATH 后重启进程。"
    ) from exc


# 本模块对外暴露的公共 API（schema 类型供 DeepSearch 框架及使用者引用）
__all__ = [
    "MedicalKnowledgeBasePlugin",
    "Guideline",
    "RetrievalResult",
    "AnswerWithCitations",
    "KnowledgeBaseEngine",
    "RAGPipeline",
]


class MedicalKnowledgeBasePlugin:
    """
    DeepSearch 医疗知识库插件

    提供医疗领域知识库的检索与问答能力，支持：
    - 诊疗指南的智能问答
    - 多级检索（向量 + BM25关键词 + 知识图谱）
    - 引用溯源（每个回答附带指南原文引用）
    - 版本对比（新旧版指南差异检测）

    用法:
        plugin = MedicalKnowledgeBasePlugin()
        plugin.load_demo_guidelines()
        result = plugin.search("空腹血糖正常值是多少？")
        print(result["answer"])
    """

    def __init__(self):
        self.engine = KnowledgeBaseEngine()
        self.pipeline = RAGPipeline(self.engine)

    # ---------- DeepSearch 标准接口 ----------

    def search(self, query: str, top_k: int = 5, **kwargs) -> dict:
        """
        搜索知识库（DeepSearch标准接口）

        Args:
            query: 查询文本
            top_k: 返回结果数
            **kwargs: 其他参数（disease, latest_only等）

        Returns:
            {
                "answer": str,
                "citations": [{"content": str, "source": str, "score": float}],
                "total_found": int,
                "plugin": "medical_kb"
            }
        """
        disease = kwargs.get("disease", "")
        include_version_compare = kwargs.get("include_version_compare", False)

        result = self.pipeline.answer(
            question=query,
            top_k=top_k,
            disease=disease,
            include_version_compare=include_version_compare,
        )

        citations = []
        for r in (result.citations or []):
            clause = r.clause
            if not clause:
                continue
            citations.append({
                "content": clause.content or "",
                "source": f"{clause.guideline_id or ''} | {clause.clause_num or ''} {clause.title or ''}",
                "score": getattr(r, "score", 0.0) or 0.0,
                "method": getattr(r, "method", "") or "",
                "evidence_level": clause.evidence_level or "",
                "recommendation": clause.recommendation or "",
            })

        return {
            "answer": result.answer or "",
            "citations": citations,
            "total_found": len(result.citations or []),
            "plugin": "medical_kb",
            "retrieval_methods": result.retrieval_methods,
        }

    def batch_search(self, queries: list[str], **kwargs) -> list[dict]:
        """批量搜索"""
        return [self.search(q, **kwargs) for q in queries]

    # ---------- 知识库管理 ----------

    def load_guideline(self, guideline: Guideline):
        """加载诊疗指南"""
        self.engine.load_guideline(guideline)

    def load_guidelines(self, guidelines: list[Guideline]):
        """批量加载指南"""
        self.engine.load_guidelines(guidelines)
        self.engine.build_index()

    def load_demo_guidelines(self):
        """加载内置示例指南（糖尿病+高血压）"""
        from medical_ai_innovation.knowledge_base.demo_data import get_demo_guidelines
        self.load_guidelines(get_demo_guidelines())

    def get_guideline_list(self) -> list[dict]:
        """获取已加载的指南列表"""
        return self.engine.get_guideline_summary()

    def get_stats(self) -> dict:
        """获取知识库统计"""
        return self.engine.stats

    # ---------- 工具方法 ----------

    def explain(self, query: str) -> dict:
        """解释检索过程"""
        return self.pipeline.explain_retrieval(query)

    def reset(self):
        """重置对话上下文"""
        self.pipeline.reset_context()

    @property
    def plugin_info(self) -> dict:
        """插件元信息"""
        return {
            "name": "medical_kb",
            "display_name": "医疗知识库",
            "description": "基于诊疗指南的医疗知识库检索与问答",
            "version": "1.0.0",
            "author": "leppardwang",
            "languages": ["zh"],
            "domains": ["healthcare", "medical"],
            "guidelines_count": len(self.engine.guidelines),
            "knowledge_base": "中国2型糖尿病防治指南(2024版/2020版), 中国高血压防治指南(2024版)",
        }