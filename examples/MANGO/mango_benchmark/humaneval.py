import asyncio
from mango_benchmark.benchmark import BaseBenchmark
from typing import Any, Tuple, List
from sentence_transformers import SentenceTransformer
from human_eval.data import write_jsonl, stream_jsonl, read_problems
from human_eval.evaluation import evaluate_functional_correctness, evaluate_correctness
from human_eval.execution import check_correctness
import textgrad as tg

class HUMANEVALBenchmark(BaseBenchmark):
    def __init__(self, name: str, file_path: str, log_path: str, dataset_type: str, embed_model: SentenceTransformer):
        super().__init__(name, file_path, log_path, dataset_type, embed_model)

        self.problems = read_problems(file_path)
        if dataset_type == "train":
            task_public_tests = [item['public_test'] for item in self.data]
            task_entry_points = [item['entry_point'] for item in self.data]
            self.train_public_tests = task_public_tests[:self.train_tasks_endpoint]
            self.train_entry_points = task_entry_points[:self.train_tasks_endpoint]
            self.valid_public_tests = task_public_tests[self.train_tasks_endpoint:]
            self.valid_entry_points = task_entry_points[self.train_tasks_endpoint:]
        elif dataset_type == "test":
            self.test_public_tests = [item['public_test'] for item in self.data]
            self.test_entry_points = [item['entry_point'] for item in self.data]
        
    def extract_model_answer(self, text: str) -> str:
        return 
    
    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        tid, answer = answer_gt[0], answer_gt[1]
        correctness = check_correctness(self.problems[tid], answer, timeout=10.0)
        correct, report = correctness["passed"], correctness["result"] # report用来写loss的
        return correct, report
    
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        write_jsonl(f"{self.test_dir}/answer.jsonl", answers)
        score = evaluate_functional_correctness(f"{self.test_dir}/answer.jsonl", answers, k = [1], problems = self.problems, timeout=10.0, ignore_incomplete=True)['pass@1']
        return score
    