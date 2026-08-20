import networkx as nx
import time
from PolicyGradient import PolicyGradient, RL_Environment
from human_eval.data import stream_jsonl, write_jsonl
from dotenv import load_dotenv
import textgrad as tg
from textgrad.loss import MultiFieldTokenParsedEvaluation, MultiFieldEvaluation
import sympy as sp
from sympy.parsing.latex import parse_latex
import json
import re
import asyncio
from sentence_transformers import SentenceTransformer
model_embedding = SentenceTransformer("./hugging_face/all-MiniLM-L6-v2")
from mango_benchmark.benchmark import BaseBenchmark
# device = torch.device('cuda' if torch.cuda.is_available() else "cpu")

load_dotenv(override=True)
llm_api = tg.get_engine("gpt-4o-mini", is_async = True)
tg.set_backward_engine(llm_api, override=True)
tools = None
skip_k = 1
# browser_toolkit_schema = {'type': 'function', 'function': {'name': 'browse_url', 'description': 'A powerful toolkit which can simulate the browser interaction to\nsolve the task which needs multi-step actions.', 'strict': True, 'parameters': {'properties': {'task_prompt': {'type': 'string', 'description': 'The task prompt to solve.'}, 'start_url': {'type': 'string', 'description': 'The start URL to visit. It should be usually \"https://www.google.com/\", unless website specified.'}, 'round_limit': {'type': ['integer', 'null'], 'description': 'The round limit to solve the task.\n(default: :obj:`12`).'}}, 'required': ['task_prompt', 'start_url', 'round_limit'], 'type': 'object', 'additionalProperties': False}}}
# tools = [
#     browser_toolkit_schema,
#     ImageAnalysisToolkit().get_tools()[1].openai_tool_schema,
#     # DocumentProcessingToolkit().get_tools()[0].openai_tool_schema,
#     # SearchToolkit().get_tools()[2].openai_tool_schema,
#     # ArxivToolkit().get_tools()[0].openai_tool_schema,
# ]

# Train the RL agent to choose correct node in the graph

FIXED_SYSTEM_PROMPT = '''
You must base your reasoning ONLY on:
- TOTAL_TASK
- CURRENT_STEP
- previous executed steps
Do not introduce new assumptions unless necessary and explicitly stated.
'''

def Training_RL(f, G:nx.DiGraph, env:RL_Environment, RL_Agent:PolicyGradient, train_benchmark:BaseBenchmark, tid_to_path):

    for i in range(train_benchmark.num_train_gts):
        tid = train_benchmark.train_ids[i]
        tvec = train_benchmark.train_prompts_vecs[i] # 每个任务prompt的embedding向量
        task_workflow = train_benchmark.train_workflows[i]

        # print(f"\n[TRAININGPG] Training episode = {episode+1} for task {i+1} / {num_tasks}", file=f, flush=True)
        gt_path = tid_to_path[tid]
        current_node = 0
        current_node_neighbours = []
        step_num = 0

        # One task process
        done = False
        while True:
            # print(f"\n[TRAININGPG] Training step = {env.step_num}", file=f, flush=True)
            if step_num < len(task_workflow):
                cur_task = task_workflow[step_num]
            else:
                cur_task = "Give the final answer of a reasoning question without any thought process and title."
            
            # RL Process
            if step_num == 0:
                current_node = gt_path[1]
                current_node_neighbours = list(G.successors(current_node))
                step_num += 1
            else:
                observation = env.get_ob(current_node_neighbours, cur_task, tvec)
                action = RL_Agent.choose_action(observation)
                step_num += 1
                reward, done, _, current_node, current_node_neighbours = env.step(action, step_num, current_node_neighbours, gt_path)
                RL_Agent.store_transition(observation, action, reward)

            if done:
                # print(f"\n[TRAININGPG] Training step = {env.step_num}", file=f, flush=True)
                if len(RL_Agent.ep_as) > 0:
                    RL_Agent.learn()
                break

def Evaluation_RL(f, G:nx.DiGraph, env:RL_Environment, RL_Agent:PolicyGradient, train_benchmark:BaseBenchmark, tid_to_path):
    
    start = time.time()
    num_correct = 0
    print(f"\n[EVALUATION] Evaluation", file=f, flush=True)
    for i in range(train_benchmark.num_valid_gts):
        tid = train_benchmark.valid_ids[i]
        tvec = train_benchmark.valid_prompts_vecs[i] # 任务prompt的embedding向量
        task_workflow = train_benchmark.valid_workflows[i]

        # print(f"\n[EVALUATION] Evaluation for task {i+1} / {num_tasks}", file=f, flush=True)
        gt_path = tid_to_path[tid]
        current_node = 0
        current_node_neighbours = []
        step_num = 0

        done = False
        while True:
            # print(f"\n[EVALUATION] Evaluation step = {env.step_num}", file=f, flush=True)
            if step_num < len(task_workflow):
                cur_task = task_workflow[step_num]
            else:
                cur_task = "Give the final answer of a reasoning question without any thought process and title."
                
            # RL Process
            if step_num == 0:
                current_node = gt_path[1]
                current_node_neighbours = list(G.successors(current_node))
                step_num += 1
                reward = 1.0
            else:
                observation = env.get_ob(current_node_neighbours, cur_task, tvec)
                action = RL_Agent.choose_action(observation)
                step_num += 1
                reward, done, _, current_node, current_node_neighbours = env.step(action, step_num, current_node_neighbours, gt_path)

            if reward == 0.0: 
                print(f"Task {tid} Node {current_node} Step {step_num} incorrect. Should be Node " + str(gt_path[step_num]), file=f, flush=True)
                break

            if done:
                # print(f"\n[EVALUATION] Evaluation step = {env.step_num}", file=f, flush=True)
                num_correct += 1
                break
    
    score = num_correct / train_benchmark.num_valid_gts
    return score, time.time() - start

async def Training_RL_TG(f, G:nx.DiGraph, env:RL_Environment, RL_Agent:PolicyGradient, episode, concurrency, train_benchmark:BaseBenchmark, tid_to_path, model_string):

    planner_model = G.nodes[0]['planner_model']
    # planner_system_prompt = planner_model.system_prompt

    # plan_optimizer = tg.TextualGradientDescent(engine=llm_api, parameters=[planner_system_prompt], constraints=["Do not grow the system prompt too much"])
    # plan_optimizer = tg.TextualGradientDescent(engine=llm_api, parameters=[planner_system_prompt])
    num_tasks = train_benchmark.num_train_gts
    # plan_losses = []
    updated_nodes = []
    
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [train_benchmark.Training_Task(f, G, env, planner_model, RL_Agent, episode, i, num_tasks, updated_nodes, semaphore, tid_to_path, model_string) for i in range(num_tasks)]
    await asyncio.gather(*tasks, return_exceptions=False)

    # plan_optimizer.zero_grad()
    # total_plan_loss = tg.sum(plan_losses)
    # total_plan_loss.backward()
    # await plan_optimizer.step()
    
    updated_nodes = list(set(updated_nodes))
    optimize_tasks = [executor_optimize(f, G, node_num, semaphore) for node_num in updated_nodes]
    await asyncio.gather(*optimize_tasks, return_exceptions=False)

async def executor_optimize(f, G:nx.DiGraph, node_num, semaphore):
    async with semaphore:
        print(f"Currently optimize node {node_num}", file=f, flush=True)
        executor_model:tg.BlackboxLLM = G.nodes[node_num]["executor_model"]
        updated_system_prompt = G.nodes[node_num]['updated_system_prompt']
        # executor_optimizer = tg.TextualGradientDescent(engine=llm_api, parameters=[updated_system_prompt], constraints=["Do not grow the system prompt too much"])
        executor_optimizer = tg.TextualGradientDescent(engine=llm_api, parameters=[updated_system_prompt])
        executor_optimizer.zero_grad()
        total_execute_loss = tg.sum(G.nodes[node_num]["execute_losses"])
        print("Loss: " + total_execute_loss.value, file=f, flush=True)
        total_execute_loss.backward()
        await executor_optimizer.step()
        print("NEW_SYSTEM_PROMPT:", file=f, flush=True)
        print(updated_system_prompt.value + FIXED_SYSTEM_PROMPT, file=f, flush=True)
        G.nodes[node_num]['updated_system_prompt_vector'] = model_embedding.encode(updated_system_prompt.value)
        executor_model.system_prompt.value = updated_system_prompt.value + FIXED_SYSTEM_PROMPT
        G.nodes[node_num]["execute_losses"] = []

async def Evaluation_RL_TG(f, G:nx.DiGraph, env:RL_Environment, RL_Agent:PolicyGradient, concurrency, starting_nodes_to_vecs, train_benchmark:BaseBenchmark, model_string):
    
    answers = []
    num_tasks = train_benchmark.num_valid_gts
    planner_model = G.nodes[0]['planner_model']
    
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [train_benchmark.Evaluation_Task(f, G, env, planner_model, RL_Agent, i, num_tasks, answers, semaphore, starting_nodes_to_vecs, model_string) for i in range(num_tasks)]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    score = train_benchmark.evaluate_all_answers(answers)
    return score, results

async def Evaluation_Test(f, test_dir, G:nx.DiGraph, env:RL_Environment, RL_Agent:PolicyGradient, concurrency, starting_nodes_to_vecs, test_benchmark:BaseBenchmark, model_string):
    
    start = time.time()
    answers = []
    num_tasks = test_benchmark.num_test_gts
    planner_model = G.nodes[0]['planner_model']

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [test_benchmark.Test_Task(f, G, env, planner_model, RL_Agent, i, num_tasks, answers, semaphore, starting_nodes_to_vecs, model_string) for i in range(num_tasks)]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    score = test_benchmark.evaluate_all_answers(answers)
    # used for debugging
    write_jsonl(f"{test_dir}/Test_Ans.jsonl", results)
    return score, time.time() - start

async def plan_loss_fn(task: tg.Variable, total_plan: tg.Variable) -> tg.Variable:
    role_descriptions = [
        "Total query prompt for the task",
        "Total plan for the task"
    ]
    
    evaluation_instruction = "Think about the task and its total plan. In the planner role, is the total plan correct and complete for solving this task?"
    eval_instruction = tg.Variable(evaluation_instruction, requires_grad=False, role_description="evaluation instruction for the task plan")
    # loss_fn = MultiFieldEvaluation(
    loss_fn = MultiFieldTokenParsedEvaluation(
        eval_instruction,
        role_descriptions=role_descriptions,
        engine=llm_api,
        parse_tags=["<PLAN_EVALUATION>", "</PLAN_EVALUATION>"]
    )

    inputs = [task, total_plan]
    return await loss_fn.async_forward(inputs)

async def execute_loss_fn(sub_task: tg.Variable, response: tg.Variable, total_task_answer: tg.Variable) -> tg.Variable:
    role_descriptions = [
        "Separated sub-task prompt",
        "Language model response for the separated sub-task",
        "Total task prompt and its answer"
    ]
    
    evaluation_instruction = "You are a smart language model that evaluates the response for the task. You do not solve task or propose new responses, only evaluate model response critically and give very concise feedback."
    eval_instruction = tg.Variable(evaluation_instruction, requires_grad=False, role_description="evaluation instruction for the task step")
    # loss_fn = MultiFieldEvaluation(
    loss_fn = MultiFieldTokenParsedEvaluation(
        eval_instruction,
        role_descriptions=role_descriptions,
        engine=llm_api,
        parse_tags=["<EXECUTE_EVALUATION>", "</EXECUTE_EVALUATION>"]
    )

    inputs = [sub_task, response, total_task_answer]
    return await loss_fn.async_forward(inputs)

# async def execute_loss_fn(sub_task: tg.Variable, response: tg.Variable, total_task_answer: tg.Variable) -> tg.Variable:
#     from textgrad.tasks.big_bench_hard import string_based_equality_fn
#     from textgrad.autograd.string_based_ops import StringBasedFunction

#     fn_purpose = "The runtime of string-based function that checks if the prediction is correct."
#     eval_fn = StringBasedFunction(string_based_equality_fn, function_purpose=fn_purpose)
#     inputs = dict(sub_task=sub_task, response=response, total_task_answer=total_task_answer)
#     return await eval_fn(inputs)

# DROP evaluation
import string
from collections import Counter
def normalize_answer(s: str):
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

def cal_f1(pred, gt):
    prediction_tokens = normalize_answer(pred).split()
    ground_truth_tokens = normalize_answer(gt).split()
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0
    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return f1

def cal_drop_f1(answers):
    """
    Compute the F1 score between prediction and ground truth answers.
    """
    total_score = 0
    for pred, gt in answers:
        f1_scores = []
        for groud_truth in gt:
            f1_scores.append(cal_f1(pred, groud_truth))
        uni_score = max(f1_scores)
        total_score += uni_score
    total_score /= len(answers)
    return total_score

import re
def extract_number(text: str):
    matches = re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|\d+\.\d+", str(text))
    if matches:
        last_number = matches[-1].replace(",", "")
        try:
            return float(last_number)
        except ValueError:
            return None
    else:
        return None

def cal_gsm8k_acc(answers):
    # assert len(gts) == len(answers), "Number of answers and gts are different."
    correct_num = 0
    for answer, gt in answers:
        if abs(extract_number(answer) - extract_number(gt)) <= 1e-6:
            correct_num += 1
    return correct_num / len(answers)

def cal_gpqa_acc(answers):
    correct_num = 0
    for answer, gt in answers:
        if answer == gt:
            correct_num += 1
    return correct_num / len(answers)

def is_latex_correct(ans: str, gt: str, tol: float = 1e-6) -> bool:
    try:
        # 尝试把 latex 解析成 Sympy 表达式
        ans_expr = parse_latex(ans)
        gt_expr = parse_latex(gt)

        # 先尝试符号化简比较
        if sp.simplify(ans_expr - gt_expr) == 0:
            return True

        # 如果是数值，尝试数值比较
        ans_val = sp.N(ans_expr)
        gt_val = sp.N(gt_expr)
        if abs(ans_val - gt_val) < tol:
            return True
        return False
    except Exception as e:
        # 如果解析失败，就 fallback 用字符串比较
        return ans.strip() == gt.strip()

def cal_math_acc(answers):
    # assert len(gts) == len(answers), "Number of answers and gts are different."
    correct_num = 0
    for answer, gt in answers:
        if len(gt) == 1:
            if is_latex_correct(answer, gt[0]):
                correct_num += 1
        else:
            if gt == ["-1", "2"] and ("-1" in answer and "2" in answer):
                correct_num += 1
            elif gt == ["\\frac{\\pi}{4}", "\\frac{5\\pi}{4}"] and ("\\frac{\\pi}{4}" in answer and "\\frac{5\\pi}{4}" in answer):
                correct_num += 1
    return correct_num / len(answers)

def save_system_prompt(G:nx.DiGraph, train_dir):
    # Save Executor prompt
    system_prompts = []
    for node in G:
        if node != 0:
            system_prompts.append({node: G.nodes[node]['updated_system_prompt'].value})
        else:
            system_prompts.append({node: G.nodes[node]['planner_model'].system_prompt.value})
    write_jsonl(train_dir + '/prompt.jsonl', system_prompts)
    # Save Planner prompt
    # planner_model: tg.BlackboxLLM = G.nodes[0]['planner_model']
    # with open(train_dir + '/planner_system_prompt.txt', "w", encoding="utf-8") as f:
    #     f.write(planner_model.system_prompt.value)

def load_system_prompt(G:nx.DiGraph, train_dir):
    # Load Executor prompt
    system_prompts = stream_jsonl(train_dir + '/prompt.jsonl')
    for system_prompt in system_prompts:
        node, updated_system_prompt = next(iter(system_prompt.items()))
        node = int(node)
        if node != 0:
            # updated_system_prompt = tg.Variable(updated_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies the behavior and strategies for the QA task.")
            G.nodes[node]['updated_system_prompt_vector'] = model_embedding.encode(updated_system_prompt)
            G.nodes[node]['updated_system_prompt'] = tg.Variable(updated_system_prompt, requires_grad=True, role_description="structured system prompt to a language model that specifies the behavior and strategies for the QA task. Its content will be updated.")
            if node == 1:
                G.nodes[node]['executor_model'].system_prompt = updated_system_prompt
            else:
                G.nodes[node]['executor_model'].system_prompt = updated_system_prompt + FIXED_SYSTEM_PROMPT
        else:
            G.nodes[node]['planner_model'].system_prompt = tg.Variable(updated_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies the behavior and strategies for the QA task.")
    # Load Planner prompt
    # planner_model:tg.BlackboxLLM = G.nodes[0]['planner_model']
    # with open(train_dir + '/planner_system_prompt.txt', "r", encoding="utf-8") as f:
    #     planner_system_prompt = f.read()
    # # planner_system_prompt = tg.Variable(planner_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies task plan for the QA task.")
    # planner_system_prompt = tg.Variable(planner_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies the behavior and strategies for the QA task.")
    # planner_model.system_prompt = planner_system_prompt
    
    # # Load Executor prompt
    # system_prompts = stream_jsonl(PG_dir + '/prompt.jsonl')
    # for system_prompt in system_prompts:
    #     node, updated_system_prompt = next(iter(system_prompt.items()))
    #     node = int(node)
    #     updated_system_prompt = tg.Variable(updated_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies the behavior and strategies for the QA task.")
    #     G.nodes[node]['updated_system_prompt'] = updated_system_prompt
    #     if node != 1:
    #         G.nodes[node]['executor_model'].system_prompt = tg.sum([G.nodes[node]["fixed_system_prompt"], updated_system_prompt])
    #     else:
    #         G.nodes[node]['executor_model'].system_prompt = updated_system_prompt
    
    # # Load Planner prompt
    # planner_model:tg.BlackboxLLM = G.nodes[0]['planner_model']
    # with open(PG_dir + '/planner_system_prompt.txt', "r", encoding="utf-8") as f:
    #     planner_system_prompt = f.read()
    # planner_system_prompt = tg.Variable(planner_system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies task plan for the QA task.")
    # planner_model.system_prompt = planner_system_prompt

IMPROVE_CODE_PROMPT = "The previous solution failed some test cases. Please analyze the problem carefully and provide an improved solution that addresses all edge cases and requirements."
