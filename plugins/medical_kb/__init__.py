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
import sys
import os
from typing import Optional

# 将 medical-ai-innovation 加入路径（相对路径，安装后可不依赖）
try:
    from medical_ai_innovation.knowledge_base.schemas import (
        Guideline, RetrievalResult, AnswerWithCitations
    )
    from medical_ai_innovation.knowledge_base.kb_engine import KnowledgeBaseEngine
    from medical_ai_innovation.knowledge_base.rag_pipeline import RAGPipeline
except ImportError:
    # 开发模式：通过相对路径查找
    _plugin_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.abspath(os.path.join(_plugin_dir, "..", "..", ".."))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from medical_ai_innovation.knowledge_base.schemas import (
        Guideline, RetrievalResult, AnswerWithCitations
    )
    from medical_ai_innovation.knowledge_base.kb_engine import KnowledgeBaseEngine
    from medical_ai_innovation.knowledge_base.rag_pipeline import RAGPipeline


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
        for r in result.citations:
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
            "answer": result.answer,
            "citations": citations,
            "total_found": len(result.citations),
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


# ========== 命令行测试 ==========

def main():
    """测试插件"""
    print("🩺 Medical Knowledge Base Plugin for DeepSearch")
    print("=" * 50)

    plugin = MedicalKnowledgeBasePlugin()
    plugin.load_demo_guidelines()

    stats = plugin.get_stats()
    print(f"📚 已加载 {stats.get('guidelines', 0)} 部指南")
    print()

    while True:
        try:
            q = input("🧑 请输入医疗问题 (输入 /quit 退出): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not q:
            continue
        if q.lower() in ("/quit", "/exit"):
            break

        result = plugin.search(q, include_version_compare=True)
        answer_preview = result["answer"][:300]
        print(f"\n🤖 {answer_preview}")
        if len(result["answer"]) > 300:
            print("   ...(回答已截断，完整内容请查看引用)")
        print(f"📚 引用 {result['total_found']} 条")
        for i, c in enumerate(result["citations"][:3], 1):
            source = c.get("source", "未知来源")
            score = c.get("score", 0)
            print(f"  [{i}] {source} (score: {score})")
        print()


if __name__ == "__main__":
    main()