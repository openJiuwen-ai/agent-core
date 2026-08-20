import asyncio
import re
from mango_benchmark.benchmark import BaseBenchmark
from typing import Any, Tuple, List
from sentence_transformers import SentenceTransformer

class GSM8KBenchmark(BaseBenchmark):
    def __init__(self, name: str, file_path: str, log_path: str, dataset_type: str, embed_model: SentenceTransformer):
        super().__init__(name, file_path, log_path, dataset_type, embed_model)
    
    def extract_number(self, text: str):
        matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
        if matches:
            last_number = matches[-1].replace(",", "")
            try:
                return float(last_number)
            except ValueError:
                return None
        else:
            return None
    
    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        answer, gt = answer_gt[0], answer_gt[1]
        return 1.0 if abs(self.extract_number(answer) - self.extract_number(gt)) <= 1e-6 else 0.0
    
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        total_score = 0.0
        for answer_gt in answers:
            total_score += self.evaluate_answer(answer_gt)
        return total_score / len(answers)