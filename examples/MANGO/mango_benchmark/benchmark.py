import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple, Dict
from pydantic import BaseModel, Field
import aiofiles
import pandas as pd
from tqdm.asyncio import tqdm_asyncio
from human_eval.data import write_jsonl, stream_jsonl
from sentence_transformers import SentenceTransformer
import re
import textgrad as tg
from PolicyGradient import RL_Environment, PolicyGradient
from utilities import parse_steps, get_response, get_init_node, get_choice_response, extract_boxed, execute_loss_fn, exec_code
import networkx as nx

plan_content = '''
TOTAL_TASK: {total_task}
For the TOTAL_TASK, generate a clear and concise workflow consisting of 1 to {max_step} steps. Add an opening tag \"(<serial num>)\" and a closing tag \"(/<serial num>)\" for each step like this:
(1) STEP_TEXT (/1)
(2) STEP_TEXT (/2)
...
Do not provide final answer.
'''
skip_k = 1

class BaseBenchmark(ABC):
    def __init__(
        self, name: str, file_path: str, test_dir: str, dataset_type: str, embed_model: SentenceTransformer) -> None:
        self.name = name
        self.file_path = file_path
        self.test_dir = test_dir
        self.embed_model = embed_model
        self.data = list(stream_jsonl(file_path))
        self.max_step = 4 if name == "math" or name == "gpqa" or name == "mmlu" else 3
        
        if dataset_type == "train":
            self.task_ids = [item['task_id'] for item in self.data]
            task_prompts = [item['prompt'] for item in self.data]
            task_prompts_vecs = [embed_model.encode(question) for question in task_prompts]
            self.task_workflows = [item['workflow'] for item in self.data]
            task_gts = [item['gt'] for item in self.data]

            self.train_tasks_endpoint = round(len(self.data) * 0.8)
            self.train_ids = self.task_ids[:self.train_tasks_endpoint]
            self.train_prompts = task_prompts[:self.train_tasks_endpoint]
            self.train_prompts_vecs = task_prompts_vecs[:self.train_tasks_endpoint]
            self.train_workflows = self.task_workflows[:self.train_tasks_endpoint]
            self.train_gts = task_gts[:self.train_tasks_endpoint]
            self.num_train_gts = self.train_tasks_endpoint
            
            self.valid_ids = self.task_ids[self.train_tasks_endpoint:]
            self.valid_prompts = task_prompts[self.train_tasks_endpoint:]
            self.valid_prompts_vecs = task_prompts_vecs[self.train_tasks_endpoint:]
            self.valid_workflows = self.task_workflows[self.train_tasks_endpoint:]
            self.valid_gts = task_gts[self.train_tasks_endpoint:]
            self.num_valid_gts = len(self.data) - self.train_tasks_endpoint
        elif dataset_type == "test":
            self.test_ids = [item['task_id'] for item in self.data]
            self.test_prompts = [item['prompt'] for item in self.data]
            self.test_prompts_vecs = [embed_model.encode(question) for question in self.test_prompts]
            self.test_gts = [item['gt'] for item in self.data]
            self.num_test_gts = len(self.test_ids)
            
    def save_results_to_jsonl(self, results: List[Dict]):
        write_jsonl(f"{self.test_dir}/answer.jsonl", results)
    
    @abstractmethod
    def evaluate_answer(self, answer_gt: Tuple[Any, ...]) -> float:
        pass

    # Evaluate all the answers
    @abstractmethod
    def evaluate_all_answers(self, answers: List[Tuple[Any, ...]]) -> float:
        pass
    
    async def Training_Task(self, f, G:nx.DiGraph, env:RL_Environment, planner_model:tg.BlackboxLLM, RL_Agent:PolicyGradient, 
                            episode, task_i, num_tasks, updated_nodes, semaphore, tid_to_path, model_string):
        async with semaphore:
            tid = self.train_ids[task_i]
            task_prompt = self.train_prompts[task_i]
            file_path = ""
            gt_path = tid_to_path[tid]
            current_node = 0
            current_node_neighbours = []
            step_num = 0
            tvec = self.train_prompts_vecs[task_i]
            # public_tests = self.train_public_tests[task_i] if self.train_public_tests else None

            print(f"\n[TRAININGPG] Training episode = {episode+1} for task {task_i+1} / {num_tasks}", file=f, flush=True)
            print(f"\n[TRAININGPG] Training episode = {episode+1} for task {task_i+1} / {num_tasks}")
            print(f"\n[TRAININGPG] Training_RL_TG step = 0", file=f, flush=True)
            print(f"{tid} Problem: {task_prompt}", file=f, flush=True)

            history_info = []
            # history_message = [{"role": "user", "content": f"TOTAL_TASK: {task_prompt}"}]
            history_message = []
            plan_list = []
            rest_task = ""
            path = [0]
            # One task process
            planner_prompt = plan_content.format(total_task=task_prompt, max_step=self.max_step)
            planner_prompt = tg.Variable(planner_prompt, requires_grad=False, role_description="prompt for current sub-task")
            cur_plan = await planner_model.async_forward(planner_prompt, temperature=0.7, memory=[])
            print(cur_plan.value, file=f, flush=True)
            split_plan = parse_steps(cur_plan.value)
            
            done = False
            t = 0
            while t < len(split_plan):
                cur_task = split_plan[t]
                if step_num == 0:
                    current_node = gt_path[1]
                    current_node_neighbours, current_node_neighbours_step = env.get_neighbours(current_node, skip_k)
                    skip_steps = 1
                    step_num += 1
                else:
                    observation = env.get_ob(current_node_neighbours, cur_task, tvec)
                    action = RL_Agent.choose_action(observation)
                    step_num += 1
                    reward, done, _, current_node, current_node_neighbours, current_node_neighbours_step, skip_steps = env.step_k(action, step_num, current_node_neighbours, current_node_neighbours_step, gt_path, skip_k)
                    RL_Agent.store_transition(observation, action, reward)
                
                path.append(current_node)
                
                if done:
                    rest_task = "\n".join(split_plan[t:])
                    break
                
                next_t = t + skip_steps
                cur_task = "\n".join(split_plan[t:next_t])
                t = next_t
                plan_list.append(cur_task)
                
                print(f"\n[TRAININGPG] Training_RL_TG step = {step_num}", file=f, flush=True)
                print(f"{tid} Current task: {cur_task}", file=f, flush=True)
                if self.name == "humaneval" or self.name == "mbpp":
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nPay attention to the order of edge cases and generate a clear and concise result."
                else:
                    # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nFocus on solving the CURRENT_STEP and generate a clear and concise result."
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nFocus on solving the CURRENT_STEP and generate a concise result."
                executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
                executor_model = G.nodes[current_node]["executor_model"]

                res = await executor_model.async_forward(executor_prompt, tools=None, temperature=0.7, file_path=file_path, memory=history_message)
                response = res.value

                print(f"{tid} Response: {response}", file=f, flush=True)
                history_message.append({"role": "user", "content": cur_task})
                history_message.append({"role": "assistant", "content": response})
                res = tg.Variable(response, requires_grad=False, role_description="prompt for current sub-task")
                history_info.append(res)
            
            if not done:
                current_node = 1
                path.append(1)
            else:
                if rest_task != "":
                    print("Rest plan:\n" + rest_task, file=f, flush=True)
                    history_message.append({"role": "user", "content": rest_task})
                    rest_answer = await get_response(history_message, model_string)
                    history_message.append({"role": "assistant", "content": rest_answer})
                    rest_task = "\n" + rest_task
            
            # Target node: get final answer
            if self.name == "humaneval" or self.name == "mbpp":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, generate an executable Python function to solve the TOTAL_TASK. \nEnsure the function name matches the one specified in the question. Wrap the code with ```python```. Do not include main function."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the code based on TOTAL_TASK and history message. Ensure the function name matches the one specified in the TOTAL_TASK.  Without prints, comments, descriptions, code block tags, test code or main function."
            elif self.name == "math":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, give the final answer of TOTAL_TASK in Latex format. Wrap the answer in \\boxed{{}}. Simplify the fraction or sqrt number to its lowest terms."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK in Latex format. Do not include any thought process, title and unit. If the answer is only number(s), give the number(s) itself without any wrapper, such as: () and []. Simplify the fraction or sqrt number to its simplest form."
            elif self.name == "drop":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Just the required number, word or phrase without unit. If you can use number, do not use number word. Please simplify redundant zeros."
            elif self.name == "gsm8k":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Ensure that your final answer is a single numerical value without any units or additional text."
            elif self.name == "gpqa" or self.name == "mmlu":
                executor_prompt = f'''TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Output the option letter based on TOTAL_TASK and previous message. 
Only output a single uppercase letter from A, B, C, D.Do NOT output anything else.

Answer:
'''
            # print(f"\n[TRAININGPG] Training_RL_TG step = {step_num}", file=f, flush=True)
            executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
            executor_model = G.nodes[1]["executor_model"]
            
            response = await executor_model.async_forward(executor_prompt, temperature=0.7, memory=history_message)
            if self.name == "humaneval" or self.name == "mbpp":
                code_res = re.search(r"(?s)```python\s*\r?\n(.*?)```", response.value, flags=re.IGNORECASE)
                if code_res:
                    response.value = "import math\n" + code_res.group(1)
                else: 
                    response.value = ""
            print(f"Total Answer {tid}:\n" + response.value, file=f, flush=True)
            if self.name == "math":
                response.value = ",".join(extract_boxed(response.value))
            print("Final Answer:\n" + response.value, file=f, flush=True)
            
            if self.name == "humaneval" or self.name == "mbpp":
                correct, report = self.evaluate_answer((tid, response.value))
            else:
                correct = self.evaluate_answer((response.value, self.train_gts[task_i])) == 1.0
            
            history_info.append(response)
            
            if len(RL_Agent.ep_as) > 0:
                print('[TRAINING] ===> Policy Learning', file=f, flush=True)
                # if correct:
                #     for b in range(len(RL_Agent.ep_rs)):
                #         RL_Agent.ep_rs[b] += 1.0
                RL_Agent.learn()
            
            if not correct:
                
                print(f'The generated result is not correct. TextGrad Optimization, Path: {path}', file=f, flush=True)
                path_len = len(path)
                
                total_plan = "\n".join(plan_list)
                total_plan += rest_task
                # update target node
                for j in range(path_len - 1, 0, -1):
                    node = path[j]
                    if node != 1:
                        updated_nodes.append(node)
                    response = history_info[j-1]
                    
                    # role_description = G.nodes[node]['role_description']
                    # role_description = tg.Variable(role_description, requires_grad=False, role_description="description of the role this agent responses for")
                    
                    if j != path_len - 1:
                        sub_task = tg.Variable(plan_list[j-1], requires_grad=False, role_description="sub_task prompt")

                    if j == path_len - 1:
                        if self.name == "humaneval" or self.name == "mbpp":
                            executor_loss = tg.Variable("The generated function is not correct. The error is:\n" + report, requires_grad=False, role_description="evaluation of the answer")
                        elif self.name == "math":
                            executor_loss = tg.Variable("The generated result is not correct. The correct solution process is:\n" + str(self.train_solutions[task_i]), requires_grad=False, role_description="evaluation of the answer")
                        else:
                            executor_loss = tg.Variable("The generated result is not correct. The correct answer is:\n" + str(self.train_gts[task_i]), requires_grad=False, role_description="evaluation of the answer")
                    else:
                        total_task_answer = task_prompt + str(self.train_solutions[task_i]) if self.name == "math" else task_prompt + str(self.train_gts[task_i])
                        total_task_answer = tg.Variable(total_task_answer, requires_grad=False, role_description="evaluation of the answer")
                        executor_loss = await execute_loss_fn(sub_task, response, total_task_answer)
                        G.nodes[node]["execute_losses"].append(executor_loss)

                # print("total plan: \n" + total_plan, file=f, flush=True)
                
    async def Evaluation_Task(self, f, G:nx.DiGraph, env:RL_Environment, planner_model:tg.BlackboxLLM, RL_Agent:PolicyGradient, 
                              task_i, num_tasks, answers, semaphore, starting_nodes_to_vecs, model_string):
        async with semaphore:
            tid = self.valid_ids[task_i]
            task_prompt = self.valid_prompts[task_i]
            file_path = ""
            current_node = 0
            current_node_neighbours = []
            step_num = 0
            tvec = self.valid_prompts_vecs[task_i] # embedding vectors

            print(f"\n[EVALUATION] for evaluation task {task_i+1} / {num_tasks}", file=f, flush=True)
            print(f"\n[EVALUATION] for evaluation task {task_i+1} / {num_tasks}")
            print(f"\n[EVALUATION] Evaluation step = 0", file=f, flush=True)
            print(f"{tid} Problem: {task_prompt}", file=f, flush=True)

            # history_message = [{"role": "user", "content": f"TOTAL_TASK: {task_prompt}"}]
            history_message = []
            rest_task = ""
            # One task process
            planner_prompt = plan_content.format(total_task=task_prompt, max_step=self.max_step)
            planner_prompt = tg.Variable(planner_prompt, requires_grad=False, role_description="prompt for current sub-task")
            cur_plan = await planner_model.async_forward(planner_prompt, temperature=0.7, memory=[])
            print(cur_plan.value, file=f, flush=True)
            split_plan = parse_steps(cur_plan.value)
            
            done = False
            t = 0
            while t < len(split_plan):
                cur_task = split_plan[t]
                if step_num == 0:
                    first_step_vec = self.embed_model.encode(cur_task)
                    current_node = get_init_node(starting_nodes_to_vecs, first_step_vec)
                    current_node_neighbours, current_node_neighbours_step = env.get_neighbours(current_node, skip_k)
                    skip_steps = 1
                    step_num += 1
                else:
                    observation = env.get_ob(current_node_neighbours, cur_task, tvec)
                    action = RL_Agent.choose_action(observation)
                    step_num += 1
                    _, done, _, current_node, current_node_neighbours, current_node_neighbours_step, skip_steps = env.step_k(action, step_num, current_node_neighbours, current_node_neighbours_step, [], skip_k)

                if done:
                    rest_task = "\n".join(split_plan[t:])
                    break
                
                next_t = t + skip_steps
                cur_task = "\n".join(split_plan[t:next_t])
                t = next_t
                
                print(f"\n[EVALUATION] Evaluation step = {step_num}", file=f, flush=True)
                print(f"{tid} Current task: {cur_task}", file=f, flush=True)
                if self.name == "humaneval" or self.name == "mbpp":
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nPay attention to the order of edge cases and generate a clear and concise result."
                else:
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nFocus on solving the CURRENT_STEP and generate a concise result."
                executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
                executor_model = G.nodes[current_node]["executor_model"]

                res = await executor_model.async_forward(executor_prompt, tools=None, temperature=0.7, file_path=file_path, memory=history_message)
                response = res.value

                print(f"{tid} Response: {response}", file=f, flush=True)
                history_message.append({"role": "user", "content": cur_task})
                history_message.append({"role": "assistant", "content": response})
                res = tg.Variable(response, requires_grad=False, role_description="prompt for current sub-task")
                
                # if not res_dict.get("successful"):
                #     planner_prompt = f"For the TOTAL_TASK, generate a clear and concise workflow consisting of 1 to {max_step} steps. Each step must start with \"(<serial number>)\" and end with \"(/<serial number>)\". Do not provide final answer."
                #     planner_prompt = tg.Variable(planner_prompt, requires_grad=False, role_description="prompt for current sub-task")
                #     cur_plan = await planner_model.async_forward(planner_prompt, temperature=0.7, memory=history_message)
                #     print("New plan:\n" + cur_plan.value, file=f, flush=True)
                #     split_plan = parse_steps(cur_plan.value)
            
            if not done:
                current_node = 1
            else:
                if rest_task != "":
                    print("Rest plan:\n" + rest_task, file=f, flush=True)
                    history_message.append({"role": "user", "content": rest_task})
                    rest_answer = await get_response(history_message, model_string)
                    history_message.append({"role": "assistant", "content": rest_answer})

            # Target node: get final answer
            if self.name == "humaneval" or self.name == "mbpp":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, generate an executable Python function to solve the TOTAL_TASK. \nEnsure the function name matches the one specified in the question. Wrap the code with ```python```. Do not include main function."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the code based on TOTAL_TASK and history message. Ensure the function name matches the one specified in the TOTAL_TASK. Without print, comment, description, test code or main function."
            elif self.name == "math":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, give the final answer of TOTAL_TASK in Latex format. Wrap the answer in \\boxed{{}}. Simplify the fraction or sqrt number to its lowest terms."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK in Latex format. Do not include any thought process, title and unit. If the answer is only number(s), give the number(s) itself without any wrapper, such as: () and []. Simplify the fraction or sqrt number to its simplest form."
            elif self.name == "drop":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Just the required number, word or phrase without unit. If you can use number, do not use number word. Please simplify redundant zeros."
            elif self.name == "gsm8k":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Ensure that your final answer is a single numerical value without any units or additional text."
            elif self.name == "gpqa" or self.name == "mmlu":
                executor_prompt = f'''TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Output the option letter based on TOTAL_TASK and previous message. 
Only output a single uppercase letter from A, B, C, D.Do NOT output anything else.

Answer:
'''
            # print(f"\n[EVALUATION] Evaluation step = {step_num}", file=f, flush=True)
            
            executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
            executor_model = G.nodes[1]["executor_model"]
            
            response = await executor_model.async_forward(executor_prompt, temperature=0.7, memory=history_message)
            if self.name == "humaneval" or self.name == "mbpp":
                code_res = re.search(r"(?s)```python\s*\r?\n(.*?)```", response.value, flags=re.IGNORECASE)
                if code_res:
                    response.value = "import math\n" + code_res.group(1)
                else: 
                    response.value = ""
            print(f"Total Answer {tid}:\n" + response.value, file=f, flush=True)
            if self.name == "math":
                response.value = ",".join(extract_boxed(response.value))
            print("Final Answer:\n" + response.value, file=f, flush=True)
            
            if self.name == "humaneval" or self.name == "mbpp":
                response.value = "import math\n" + response.value
                
                # for _ in range(3):
                #     error = Exec_Code(f, response.value, public_tests)
                #     if error != "":
                #         FIX_CODE_PROMPT = "The solution failed to pass the tests. According to the problem and error, generate new code. Ensure the function name unchanged and the necessary libraries imported. Do not include test code and print content."
                #         fixed_prompt = f"Problem: {task_prompt}Failed solution:\n{response.value}\nError: {error}"
                #         print("Fixed_prompt: " + fixed_prompt, file=f, flush=True)
                #         code = await get_response([{"role": "system", "content": FIX_CODE_PROMPT}, {"role": "user", "content": fixed_prompt}])
                #         # code = await get_response([{"role": "system", "content": "You are a code developer. For the Problem, you will fix the Failed Solution based on its Error and give the new solution."}, {"role": "user", "content": fixed_prompt}])
                #         response.value = "import math\n" + re.search(r"(?s)```python\s*\r?\n(.*?)```", code, flags=re.IGNORECASE).group(1)
                #         # response.value = "import math\n" + await get_response([{"role": "system", "content": "You will give the final code for a coding question without any thought process and title. Do not include any test process and print content, just the required function."}, {"role": "user", "content": fixed_prompt}])
                #         print("Fixed Answer: \n" + response.value, file=f, flush=True)
                #     else:
                #         break
                answers.append({"task_id": tid, "completion": response.value})
            elif self.name == "math" or self.name == "drop" or self.name == "gsm8k" or self.name == "gpqa" or self.name == "mmlu":
                # if benchmark_selected == "gsm8k":
                #     response.value = await get_response([{"role": "system", "content": "Check the Given Answer by plugging it back into the Question. If correct, return the Given Answer; else resolve the Question and give a new answer. Ensure your answer is a single numerical value without any units or additional text."}, {"role": "user", "content": f"Question: {task_prompt}\nGiven Answer: {response.value}"}])
                answers.append((response.value, self.valid_gts[task_i]))
            
            return {"tid": tid, "answer": response.value, "gt": self.valid_gts[task_i]}
    
    async def Test_Task(self, f, G:nx.DiGraph, env:RL_Environment, planner_model:tg.BlackboxLLM, RL_Agent:PolicyGradient, 
                        task_i, num_tasks, answers, semaphore, starting_nodes_to_vecs, model_string):
        async with semaphore:
            tid = self.test_ids[task_i]
            task_prompt = self.test_prompts[task_i]
            file_path = ""
            current_node = 0
            current_node_neighbours = []
            step_num = 0
            tvec = self.test_prompts_vecs[task_i]

            print(f"\n[TEST] for test task {task_i+1} / {num_tasks}", file=f, flush=True)
            print(f"\n[TEST] for test task {task_i+1} / {num_tasks}")
            print(f"\n[TEST] Test step = 0", file=f, flush=True)
            print(f"{tid} Problem: {task_prompt}", file=f, flush=True)

            # history_message = [{"role": "user", "content": f"TOTAL_TASK: {task_prompt}"}]
            history_message = []
            rest_task = ""
            # One task process
            planner_prompt = plan_content.format(total_task=task_prompt, max_step=self.max_step)
            planner_prompt = tg.Variable(planner_prompt, requires_grad=False, role_description="prompt for current sub-task")
            cur_plan = await planner_model.async_forward(planner_prompt, temperature=0.7, memory=[])
            print(cur_plan.value, file=f, flush=True)
            split_plan = parse_steps(cur_plan.value)
            
            done = False
            t = 0
            while t < len(split_plan):
                cur_task = split_plan[t]
                if step_num == 0:
                    # current_node = tid_to_init_node_dict[tid]
                    first_step_vec = self.embed_model.encode(cur_task)
                    current_node = get_init_node(starting_nodes_to_vecs, first_step_vec)
                    current_node_neighbours, current_node_neighbours_step = env.get_neighbours(current_node, skip_k)
                    skip_steps = 1
                    step_num += 1
                else:
                    observation = env.get_ob(current_node_neighbours, cur_task, tvec)
                    action = RL_Agent.choose_action(observation)
                    step_num += 1
                    _, done, _, current_node, current_node_neighbours, current_node_neighbours_step, skip_steps = env.step_k(action, step_num, current_node_neighbours, current_node_neighbours_step, [], skip_k)
                
                if done:
                    rest_task = "\n".join(split_plan[t:])
                    break
                
                next_t = t + skip_steps
                cur_task = "\n".join(split_plan[t:next_t])
                t = next_t
                
                print(f"\n[TEST] Test step = {step_num}", file=f, flush=True)
                print(f"{tid} Current task: {cur_task}", file=f, flush=True)
                if self.name == "humaneval" or self.name == "mbpp":
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nPay attention to the order of edge cases and generate a clear and concise result."
                else:
                    executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: {cur_task}\nFocus on solving the CURRENT_STEP and generate a concise result."
                executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
                executor_model = G.nodes[current_node]["executor_model"]

                res = await executor_model.async_forward(executor_prompt, tools=None, temperature=0.7, file_path=file_path, memory=history_message)
                response = res.value

                print(f"{tid} Response: {response}", file=f, flush=True)
                history_message.append({"role": "user", "content": cur_task})
                history_message.append({"role": "assistant", "content": response})
                res = tg.Variable(response, requires_grad=False, role_description="prompt for current sub-task")
                
                # if not res_dict.get("successful"):
                #     planner_prompt = f"For the TOTAL_TASK, generate a clear and concise workflow consisting of 1 to {max_step} steps. Each step must start with \"(<serial number>)\" and end with \"(/<serial number>)\". Do not provide final answer."
                #     planner_prompt = tg.Variable(planner_prompt, requires_grad=False, role_description="prompt for current sub-task")
                #     cur_plan = await planner_model.async_forward(planner_prompt, temperature=0.7, memory=history_message)
                #     print("New plan:\n" + cur_plan.value, file=f, flush=True)
                #     split_plan = parse_steps(cur_plan.value)
            
            if not done:
                current_node = 1
            else:
                if rest_task != "":
                    print("Rest plan:\n" + rest_task, file=f, flush=True)
                    history_message.append({"role": "user", "content": rest_task})
                    rest_answer = await get_response(history_message, model_string)
                    history_message.append({"role": "assistant", "content": rest_answer})

            # Target node: get final answer
            if self.name == "humaneval" or self.name == "mbpp":
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the code based on TOTAL_TASK and history message. Ensure the function name matches the one specified in the TOTAL_TASK. Without print, comment, description, code block tag, test code or main function."
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, generate an executable Python function to solve the TOTAL_TASK. \nEnsure the function name matches the one specified in the question. Wrap the code with ```python```. Do not include main function."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the code based on TOTAL_TASK and history message. Ensure the function name matches the one specified in the TOTAL_TASK. Without print, comment, description, test or main function."
            elif self.name == "math":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Based on previous messages, give the final answer of TOTAL_TASK in Latex format. Wrap the answer in \\boxed{{}}. Simplify the fraction or sqrt number to its lowest terms."
                # executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK in Latex format. Do not include any thought process, title and unit. If the answer is only number(s), give the number(s) itself without any wrapper, such as: () and []. Simplify the fraction or sqrt number to its simplest form."
            elif self.name == "drop":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Just the required number, word or phrase without unit. If you can use number, do not use number word. Please simplify redundant zeros."
            elif self.name == "gsm8k":
                executor_prompt = f"TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Give the final answer based on TOTAL_TASK. Ensure that your final answer is a single numerical value without any units or additional text."
            elif self.name == "gpqa" or self.name == "mmlu":
                executor_prompt = f'''TOTAL_TASK: {task_prompt}\nCURRENT_STEP: Output the option letter based on TOTAL_TASK and previous message. 
Only output a single uppercase letter from A, B, C, D.Do NOT output anything else.

Answer:
'''
            executor_prompt = tg.Variable(executor_prompt, requires_grad=False, role_description="prompt for current sub-task")
            executor_model = G.nodes[1]["executor_model"]
            
            response = await executor_model.async_forward(executor_prompt, temperature=0.7, memory=history_message)
            if self.name == "humaneval" or self.name == "mbpp":
                code_res = re.search(r"(?s)```python\s*\r?\n(.*?)```", response.value, flags=re.IGNORECASE)
                if code_res:
                    response.value = "import math\n" + code_res.group(1)
                else: 
                    response.value = ""
            print(f"Total Answer {tid}:\n" + response.value, file=f, flush=True)
            if self.name == "math":
                response.value = ",".join(extract_boxed(response.value))
            print("Final Answer:\n" + response.value, file=f, flush=True)
            
            if self.name == "humaneval":
                response.value = "import math\n" + response.value
                correct = False
                public_tests = self.test_public_tests[task_i] if self.test_public_tests else None
                for _ in range(3):
                    error = exec_code(f, response.value, public_tests)
                    if error != "":
                        # FIX_CODE_PROMPT = "The solution failed to pass the tests. According to the problem and error, generate new code. Ensure the function name unchanged and the necessary libraries imported. Do not include test code and print content."
                        fixed_prompt = f"Problem: {task_prompt}Wrong Answer:\n{response.value}\nError: {error}"
                        print("Fixed_prompt: " + fixed_prompt, file=f, flush=True)
                        code = await get_response([{"role": "system", "content": "You are a software developer. I will give you a problem. Solve it and pay attention to the order of edge cases."}, {"role": "user", "content": task_prompt + "\nWrap the code with ```python```. Do not include main function."}], model_string)
                        code_res = re.search(r"(?s)```python\s*\r?\n(.*?)```", code, flags=re.IGNORECASE)
                        if code_res:
                            response.value = "import math\n" + code_res.group(1)
                        else: 
                            response.value = ""
                        
                        print("Fixed Answer: \n" + response.value, file=f, flush=True)
                    else:
                        correct = True
                        break
                answers.append({"task_id": tid, "completion": response.value, "pulic_test_correct": correct})
            elif self.name == "mbpp":
                response.value = "import math\n" + response.value
                correct = False
                public_tests = self.test_public_tests[task_i] if self.test_public_tests else None
                for _ in range(3):
                    error = exec_code(f, response.value, public_tests)
                    if error != "":
                        FIX_CODE_PROMPT = "The solution failed to pass the tests. According to the problem and error, generate new code. Ensure the function name unchanged and the necessary libraries imported. Do not include test code and print content."
                        fixed_prompt = f"Problem: {task_prompt}Wrong Answer:\n{response.value}\nError: {error}"
                        print("Fixed_prompt: " + fixed_prompt, file=f, flush=True)
                        code = await get_response([{"role": "system", "content": FIX_CODE_PROMPT}, {"role": "user", "content": fixed_prompt + "\nWrap the code with ```python```. Do not include main function."}], model_string)
                        code_res = re.search(r"(?s)```python\s*\r?\n(.*?)```", code, flags=re.IGNORECASE)
                        if code_res:
                            response.value = "import math\n" + code_res.group(1)
                        else: 
                            response.value = ""
                        print("Fixed Answer: \n" + response.value, file=f, flush=True)
                    else:
                        correct = True
                        break
                answers.append({"task_id": tid, "completion": response.value, "pulic_test_correct": correct})
            elif self.name == "math" or self.name == "drop" or self.name == "gsm8k" or self.name == "gpqa" or self.name == "mmlu":
                if self.name == "gsm8k":
                    # response.value = await get_response([{"role": "system", "content": "Check the Given Answer by plugging it back into the Question. If correct, return the Given Answer; else resolve the Question and give a new answer. Ensure your answer is a single numerical value without any units or additional text."}, {"role": "user", "content": f"Question: {task_prompt}\nGiven Answer: {response.value}"}])
                    response.value = await get_response([{"role": "system", "content": "Substitute the answer into the original Question to verify. If correct, return the Given Answer; else solve the Question and provide a new answer. Ensure your answer is a single numerical value without any units or additional text."}, {"role": "user", "content": f"Question: {task_prompt}\nGiven Answer: {response.value}"}], model_string)
                if self.name == "gpqa" or self.name == "mmlu":
                    new_response = await get_choice_response([{"role": "system", "content": "Check the answer into the original question to verify. If correct, return the original choice; if not correct, solve the question again and provide a new choice. Ensure the \"choice\" is one of the four option letters."}, {"role": "user", "content": f"Question: {task_prompt}\nGiven Answer: {response.value}"}], model_string)
                    new_response = json.loads(new_response)
                    response.value = new_response["choice"]
                answers.append((response.value, self.test_gts[task_i]))
            if self.name == "gpqa" or self.name == "mmlu":
                return {"tid": tid, "process": new_response["thought_process"], "answer": response.value, "gt": self.test_gts[task_i]}
            return {"tid": tid, "answer": response.value, "gt": self.test_gts[task_i]}