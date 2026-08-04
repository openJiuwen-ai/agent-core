import asyncio
import re
from mango_benchmark.benchmark import BaseBenchmark
from typing import Any, Tuple, List
from sentence_transformers import SentenceTransformer
import string
from collections import Counter

class DROPBenchmark(BaseBenchmark):
    def __init__(self, name: str, file_path: str, log_path: str, dataset_type: str, embed_model: SentenceTransformer):
        super().__init__(name, file_path, log_path, dataset_type, embed_model)
    
    def normalize_answer(self, s: str):
        """
        Normalize answers for evaluation.
        """

        def remove_articles(text):
            return re.sub(r"\b(a|an|the)\b", " ", text)

        def white_space_fix(text):
            return " ".join(text.split())

        def remove_punc(text):
            exclude = set(string.punctuation)
            return "".join(ch for ch in text if ch not in exclude)

        return white_space_fix(remove_articles(remove_punc(s.lower())))

    def cal_f1(self, pred, gt):
        prediction_tokens = self.normalize_answer(pred).split()
        ground_truth_tokens = self.normalize_answer(gt).split()
        common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
        num_same = sum(common.values())
        if num_same == 0:
            return 0
        precision = 1.0 * num_same / len(prediction_tokens)
        recall = 1.0 * num_same / len(ground_truth_tokens)
        f1 = (2 * precision * recall) / (precision + recall)
        return f1

    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        f1_scores = []
        answer, gts = answer_gt[0], answer_gt[1]
        for gt in gts:
            f1_scores.append(self.cal_f1(answer, gt))
        return max(f1_scores)
    
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        total_score = 0.0
        for answer in answers:
            total_score += self.evaluate_answer(answer)
        total_score /= len(answers)
        return total_score