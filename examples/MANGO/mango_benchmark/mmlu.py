import asyncio
from mango_benchmark.benchmark import BaseBenchmark
from typing import Any, Tuple, List
from sentence_transformers import SentenceTransformer

class MMLUBenchmark(BaseBenchmark):
    def __init__(self, name: str, file_path: str, log_path: str, dataset_type: str, embed_model: SentenceTransformer):
        super().__init__(name, file_path, log_path, dataset_type, embed_model)
    
    # TODO: 修改为从一段话提取选项和gt比较
    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        return 1.0 if answer_gt[0] == answer_gt[1] else 0.0
    
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        total_score = 0.0
        for answer in answers:
            total_score += self.evaluate_answer(answer)
        return total_score / len(answers)