import asyncio
from math import isclose
import regex
from sympy import N, simplify
from sympy.parsing.latex import parse_latex
from sympy.parsing.sympy_parser import parse_expr
from mango_benchmark.benchmark import BaseBenchmark
from typing import Any, Tuple, List
from sentence_transformers import SentenceTransformer

class MATHBenchmark(BaseBenchmark):
    def __init__(self, name: str, file_path: str, log_path: str, dataset_type: str, embed_model: SentenceTransformer):
        super().__init__(name, file_path, log_path, dataset_type, embed_model)
        
        if dataset_type == "train":
            task_solutions = [item['solution'] for item in self.data]
            self.train_solutions = task_solutions[:self.train_tasks_endpoint]
            self.test_solutions = task_solutions[self.train_tasks_endpoint:]

        elif dataset_type == "test":
            self.test_solutions = [item['solution'] for item in self.data]
    
    def extract_model_answer(self, text: str) -> str:
        def extract_boxed(s: str):
            results = []
            i = 0
            n = len(s)

            while i < n:
                # 找到 \boxed{
                if s.startswith(r'\boxed{', i):
                    i += len(r'\boxed{')
                    depth = 1
                    buf = []

                    while i < n:
                        ch = s[i]

                        if ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                results.append(''.join(buf))
                                break

                        buf.append(ch)
                        i += 1
                i += 1
            return results
        return ",".join(extract_boxed(text))
    
    def math_equal(self, prediction, reference) -> bool:
        if str(prediction) == str(reference):
            return True

        try:
            if self.is_digit(prediction) and self.is_digit(reference):
                prediction = self.parse_digits(prediction)
                reference = self.parse_digits(reference)
                return isclose(prediction, reference, abs_tol=1e-3)
        except:
            pass

        try:
            return self.symbolic_equal(prediction, reference)
        except:
            pass

        return False

    def is_digit(self, num):
        return self.parse_digits(num) is not None

    def parse_digits(self, num):
        num = regex.sub(",", "", str(num))
        try:
            return float(num)
        except:
            if num.endswith("%"):
                num = num[:-1]
                if num.endswith("\\"):
                    num = num[:-1]
                try:
                    return float(num) / 100
                except:
                    pass
        return None

    def symbolic_equal(self, a, b):
        def _parse(s):
            for f in [parse_latex, parse_expr]:
                try:
                    return f(s)
                except:
                    pass
            return s

        a = _parse(a)
        b = _parse(b)

        try:
            if simplify(a - b) == 0:
                return True
        except:
            pass

        try:
            if isclose(N(a), N(b), abs_tol=1e-3):
                return True
        except:
            pass
        return False
    
    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        answer = answer_gt[0]
        gt = answer_gt[1]
        if len(gt) == 1:
            return 1.0 if self.math_equal(answer, gt[0]) else 0.0
        else:
            if gt == ["-1", "2"] and ("-1" in answer and "2" in answer):
                return 1.0
            elif gt == ["\\frac{\\pi}{4}", "\\frac{5\\pi}{4}"] and ("\\frac{\\pi}{4}" in answer and "\\frac{5\\pi}{4}" in answer):
                return 1.0
            else:
                return 0.0
    
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        total_score = 0.0
        for answer, gt in answers:
            total_score += self.evaluate_answer((answer, gt))
        return total_score / len(answers)