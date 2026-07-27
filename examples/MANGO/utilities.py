from collections import defaultdict
import numpy as np
import networkx as nx
from sklearn.metrics.pairwise import cosine_similarity
from human_eval.evaluation import evaluate_functional_correctness
import torch
import re
import matplotlib.pyplot as plt
import textgrad as tg
from human_eval.data import stream_jsonl, write_jsonl
import os
import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed
from token_usage import TOKEN_USAGE
from sentence_transformers import SentenceTransformer
from textgrad.loss import MultiFieldTokenParsedEvaluation, MultiFieldEvaluation

# browser_toolkit_schema = {'type': 'function', 'function': {'name': 'browse_url', 'description': 'A powerful toolkit which can simulate the browser interaction to\nsolve the task which needs multi-step actions.', 'strict': True, 'parameters': {'properties': {'task_prompt': {'type': 'string', 'description': 'The task prompt to solve.'}, 'start_url': {'type': 'string', 'description': 'The start URL to visit. It should be usually \"https://www.google.com/\" except for the specified website.'}, 'round_limit': {'type': ['integer', 'null'], 'description': 'The round limit to solve the task.\n(default: :obj:`12`).'}}, 'required': ['task_prompt', 'start_url', 'round_limit'], 'type': 'object', 'additionalProperties': False}}}
# tools = [
#     browser_toolkit_schema,
#     ImageAnalysisToolkit().get_tools()[1].openai_tool_schema,
#     DocumentProcessingToolkit().get_tools()[0].openai_tool_schema,
#     # SearchToolkit().get_tools()[2].openai_tool_schema
# ]

# {
#     "type": "function",
#     "function": {
#         "name": "browse",
#         "description": "Use the \"browse\" to perform browser operations to obtain internet information.",
#         # "strict": True,
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "query": {
#                     "type": "string",
#                     "description": "The text keywords and operations to search for"
#                 }
#             },
#             "required": ["query"],
#             # "additionalProperties": False
#         }
#     }
# },
FIXED_SYSTEM_PROMPT = '''
You must base your reasoning ONLY on:
- TOTAL_TASK
- CURRENT_STEP
- previous executed steps
Do not introduce new assumptions unless necessary and explicitly stated.
'''
client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE"))
@retry(stop=stop_after_attempt(5), wait=wait_fixed(1), retry=retry_if_exception_type(Exception), reraise=True)
async def get_response(messages, model_string = "gpt-4o-mini"):
    response = await client.chat.completions.create(
        model=model_string,
        temperature=0.7,
        top_p=0.9,
        messages=messages,
        max_tokens=2048,
    )
    usage = response.usage
    input_token = usage.prompt_tokens
    output_token = usage.completion_tokens
    TOKEN_USAGE.add_usage(input_token, output_token)
    return response.choices[0].message.content

@retry(stop=stop_after_attempt(5), wait=wait_fixed(1), retry=retry_if_exception_type(Exception), reraise=True)
async def get_choice_response(messages, model_string = "gpt-4o-mini"):
    json_schema = {
        "name": "my_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "thought_process": {"type": "string", "description": "Thinking process"},
                "choice": {"type": "string", "description": "Answer letter"}
            },
            "required": ["thought_process", "choice"],
            "additionalProperties": False
        },
    }
    response = await client.chat.completions.create(
        model=model_string,
        temperature=0.7,
        top_p=0.9,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": json_schema,
        },
        max_tokens=2048,
    )
    usage = response.usage
    input_token = usage.prompt_tokens
    output_token = usage.completion_tokens
    TOKEN_USAGE.add_usage(input_token, output_token)
    return response.choices[0].message.content

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
# def test_result(result, result_filename, answer_filename):
#     write_jsonl(result_filename, result)
#     correct = evaluate_functional_correctness(result_filename, k = [1], problem_file=answer_filename, ignore_incomplete=True)['pass@1'] == 1.0
#     return correct

def get_dataset_info(data: str):
    _, rest = data.strip().split("Content:")
    content, role_description = rest.strip().split("Role Description:")
    return content.strip(), role_description.strip()

def parse_steps(text):
    pattern = re.compile(r'\(\s*(\d+)\s*\)(.*?)\(/\s*\1\s*\)', re.DOTALL)
    matches = pattern.findall(text)
    m = [content.strip() for _, content in matches]
    # return [f"step {i}: {content}" for i, content in enumerate(m, 1)]
    return m

def extract_boxed(s):
    results = []
    i = 0
    n = len(s)

    while i < n:
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

def exec_code(f, solution, public_tests):
    # global_dict = {
    #     "math": __import__("math"),
    #     "hashlib": __import__("hashlib"),
    #     "re": __import__("re")
    # }
    error_information = ""
    for public_test in public_tests:
        code = solution + "\n" + public_test
        try:
            # with time_limit(timeout=60.0):
            exec(code, globals())
            # exec(code, global_dict)
        except AssertionError as e:
            error_information += f"The code cannot get the correct result for sample: {public_test}\n"
        except Exception as e:
            error_information += f"For the sample: {public_test} Error: {str(e)}\n"
    return error_information

def get_init_node(starting_nodes_to_vecs, first_step_vec):
    max_similarity = -1
    max_similarity_node = None
    for starting_node, starting_nodes_vec in starting_nodes_to_vecs.items():
        similarity = cosine_similarity([first_step_vec], [starting_nodes_vec])[0][0]
        if similarity > max_similarity:
            max_similarity = similarity
            max_similarity_node = starting_node
    return max_similarity_node

def build_init_node_dict(starting_nodes, starting_nodes_vecs, tvecs, tids):
    tid_to_init_node_dict = {}
    for tid, tvec in zip(tids, tvecs):
        max_similarity = -1
        max_similarity_node = None
        for i in range(len(starting_nodes)):
            similarity = cosine_similarity([tvec], [starting_nodes_vecs[i]])[0][0]
            if similarity > max_similarity:
                max_similarity = similarity
                max_similarity_node = starting_nodes[i]
        tid_to_init_node_dict[tid] = max_similarity_node
    return tid_to_init_node_dict

# TODO: 改变点结合的内容，从整句到句子包含的动作
llm_api = tg.get_engine("gpt-4o-mini", is_async = True)
tg.set_backward_engine(llm_api, override=True)

def build_graph(f, train_benchmark, model_embedding: SentenceTransformer, threshold=0.8):

    benchmark_selected, tids, workflows = train_benchmark.name, train_benchmark.train_ids, train_benchmark.train_workflows
    G = nx.DiGraph()
    node_counter = 2
    node_to_content_vecs = {}
    node_to_rd_vecs = {}
    query_to_edge = defaultdict(list)
    starting_nodes = []
    tid_to_path = {}

    # add the source node into the graph
    G.add_node(0, response = "")
    G.nodes[0]['role_description'] = "Planner"
    system_prompt = "You are a task planner. Create a concise and efficient plan that outlines the necessary steps to complete the QA task with the fewest possible actions. Ensure that you do not provide the final answer."
    # system_prompt = "You are a task planner. Create a concise and efficient plan that outlines the necessary steps to complete the QA task. Ensure no final answer."
    system_prompt = tg.Variable(system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies task plan for the QA task.")
    G.nodes[0]['planner_model'] = tg.BlackboxLLM(llm_api, system_prompt)

    G.add_node(1, response = "", execute_losses = [])
    if benchmark_selected == "humaneval" or benchmark_selected == "mbpp":
        G.nodes[1]['role_description'] = "Code developer, the assistant gives the code for this question based on history messages."
        # system_prompt = "You will give the code of a coding question without any thought process. Do not include any test process and print content. Ensure the code block tag ```python``` included."
        system_prompt = "You are a code developer. You will give the final code for a coding question without any thought process and title. Do not include any test process and print content, just the required function. Pay attention to function names and parameter types."
        # system_prompt = "You will give the code of a coding question. Please notice the history messages mentioned earlier. Do not include any test process and print content. Ensure the code block tag ```python``` included."
    elif benchmark_selected == "math":
        G.nodes[1]['role_description'] = "Answer giver, the assistant gives the answer based on history messages."
        system_prompt = "You are an answer giver. You will give the final answer of a reasoning question based on history messages. Simplify the fraction or sqrt number to its lowest terms."
    else:
        G.nodes[1]['role_description'] = "Answer giver, the assistant gives the answer based on history messages."
        system_prompt = "You are an answer giver. You will give the final answer of a reasoning question based on history messages."
    
    G.nodes[1]['content_vector'] = model_embedding.encode(system_prompt)
    
    node_to_rd_vecs[1] = model_embedding.encode(G.nodes[1]['role_description'])
    node_to_content_vecs[1] = model_embedding.encode("Give the final answer of a reasoning question without any thought process and title.")
    G.nodes[1]['updated_system_prompt_vector'] = model_embedding.encode(G.nodes[1]['role_description'])
    G.nodes[1]['updated_system_prompt'] = tg.Variable(system_prompt, requires_grad=True, role_description="structured system prompt to a somewhat capable language model that specifies the behavior and strategies for the QA task.")
    # G.nodes[1]['executor_model'] = tg.BlackboxLLM(llm_api, system_prompt + FIXED_SYSTEM_PROMPT)
    G.nodes[1]['executor_model'] = tg.BlackboxLLM(llm_api, system_prompt)

    # Counters for tracking node addition
    for tid, workflow in zip(tids, workflows):
        subtasks = workflow
        current_node_path = [0] # start from source node

        for i, subtask in enumerate(subtasks):
            
            content, role_description  = get_dataset_info(subtask)
            role_description = role_description.replace("|", ",")

            # Extract the word and compute the embedding
            abstract, concrete = content.split("|", 1)

            abstract_embedding = model_embedding.encode(abstract)
            rd_embedding = model_embedding.encode(role_description)
            
            max_similarity = -1
            max_similarity_node = -1
            for node in node_to_content_vecs:
                # if (i != 0 and node not in current_node_path and node not in starting_nodes) or (i == 0 and node in starting_nodes):
                if node not in current_node_path:
                    content_similarity = cosine_similarity([torch.tensor(abstract_embedding)], [torch.tensor(node_to_content_vecs[node])])[0][0]
                    rd_similarity = cosine_similarity([torch.tensor(rd_embedding)], [torch.tensor(node_to_rd_vecs[node])])[0][0]
                    similarity = (content_similarity + rd_similarity) / 2
                    if similarity >= threshold and similarity > max_similarity:
                        max_similarity_node = node
                        max_similarity = similarity
            
            if max_similarity_node != -1:
                current_node_path.append(max_similarity_node)
                # node_to_graph[max_similarity_node].add(tid)
                G.nodes[max_similarity_node]['subtasks'].append(concrete) # TODO: 记录该node曾经解决过的subtask内容，可提供给大模型参考
                G.nodes[max_similarity_node]['role_descriptions'].append(role_description)
                G.nodes[max_similarity_node]['tids'].append(tid)
                G.nodes[max_similarity_node]["content_vectors"].append(abstract_embedding)
                G.nodes[max_similarity_node]["rd_vectors"].append(rd_embedding)
                
                # Update the node embedding by averaging
                num_subtasks = len(G.nodes[max_similarity_node]['subtasks'])
                node_to_content_vecs[max_similarity_node] = (node_to_content_vecs[max_similarity_node] * (num_subtasks - 1) + np.array(abstract_embedding)) / num_subtasks
                node_to_rd_vecs[max_similarity_node] = (node_to_rd_vecs[max_similarity_node] * (num_subtasks - 1) + np.array(rd_embedding)) / num_subtasks
            else:
                # If no similar node found, create a new node
                G.add_node(node_counter, subtasks=[concrete], role_descriptions=[role_description], content_vectors=[abstract_embedding], rd_vectors=[rd_embedding], tids=[tid],  response="", execute_losses=[])
                node_to_content_vecs[node_counter] = abstract_embedding
                node_to_rd_vecs[node_counter] = rd_embedding
                # fixed_system_prompt = f"Target: You are a {role_description} Execute the CURRENT_STEP without extra text, explanation, comment."
                # updated_system_prompt = "Operating Principles: Focus only on the CURRENT_STEP; do not expand scope, jump ahead or solve the total task by yourself. Double-check your calculations or reasoning."
                # updated_system_prompt = fixed_system_prompt + updated_system_prompt
                # updated_system_prompt = tg.Variable(updated_system_prompt, requires_grad=True, role_description="Structured system prompt to a language model that specifies the behavior and strategies for the QA task. Its content will be updated.")
                # G.nodes[node_counter]['updated_system_prompt'] = updated_system_prompt
                # G.nodes[node_counter]['executor_model'] = tg.BlackboxLLM(llm_api, updated_system_prompt)
                current_node_path.append(node_counter)
                node_counter += 1
        # Add edges for the current subtask graph
        current_node_path.append(1) # end with target node
        tid_to_path[tid] = current_node_path
        print(f"{tid}: {current_node_path}", file=f, flush=True)
        for j in range(len(current_node_path) - 1):
            node_u = current_node_path[j]
            node_v = current_node_path[j + 1]
            if G.has_edge(node_u, node_v):
                G.edges[node_u, node_v]['tids'].append(tid)
            else:
                G.add_edge(node_u, node_v, tids=[tid])
            
            if j == 1:
                starting_nodes.append(node_u)
            
            query_to_edge[tid].append((node_u, node_v))

    for node in range(2, node_counter):
        # if len(G.nodes[node]["subtasks"]) > 1:
        #     cur_subtask = get_centroid_subtask(G.nodes[node]["subtasks"], G.nodes[node]["content_vectors"])
        # else:
        #     cur_subtask = G.nodes[node]["subtasks"][0]
        # G.nodes[node]['content_vector'] = model_embedding.encode(cur_subtask)
        
        if len(G.nodes[node]["role_descriptions"]) > 1:
            cur_role_description = get_centroid_subtask(G.nodes[node]["role_descriptions"], G.nodes[node]["rd_vectors"])
        else:
            cur_role_description = G.nodes[node]["role_descriptions"][0]
        
        # updated_system_prompt = f"You are a {cur_role_description.lower()} Previous task: {cur_subtask}\n"
        updated_system_prompt = f"You are a {cur_role_description.lower()}"
        G.nodes[node]['updated_system_prompt_vector'] = model_embedding.encode(updated_system_prompt)
        G.nodes[node]['updated_system_prompt'] = tg.Variable(updated_system_prompt, requires_grad=True, role_description="structured system prompt to a language model that specifies the behavior and strategies for the QA task. Its content will be updated.")
        G.nodes[node]['executor_model'] = tg.BlackboxLLM(llm_api, updated_system_prompt + FIXED_SYSTEM_PROMPT)
    return G, node_to_content_vecs, node_to_rd_vecs, query_to_edge, starting_nodes, tid_to_path

def get_centroid_subtask(strings, embeddings_vectors):
    centroid = np.mean(embeddings_vectors, axis=0)
    similarities = cosine_similarity(
        embeddings_vectors,
        centroid.reshape(1, -1)
    ).flatten()
    best_index = np.argmax(similarities)
    return strings[best_index]

SC_ENSEMBLE_PROMPT = """
Given the question described as follows: {problem}
Several solutions have been generated to address the given question. They are as follows: {solutions}
Carefully evaluate these solutions and identify the answer that appears most frequently across them. This consistency in answers is crucial for determining the most reliable solution.
Please output only the single letter ID (A, B, C, etc.) corresponding to the most consistent solution. Do not include any additional text or explanation.
"""

GSM8K_SOLVE_PROMPT = """
You are a highly skilled mathematician tasked with solving a math problem. Follow these steps carefully:

1. Read and understand the problem thoroughly.
2. Identify all key information, variables, and relationships.
3. Determine the appropriate mathematical concepts, formulas, or equations to use.
4. Solve the problem step-by-step, showing all your work clearly.
5. Double-check your calculations and reasoning at each step.
6. Provide a clear and concise final answer.
7. Verify your solution by plugging it back into the original problem or using an alternative method if possible.

Format your answer as follows:
- Clearly state your final answer at the end of your solution.
- Express numerical answers as precise values (avoid rounding unless specified).
- Ensure that your final answer is a single numerical value without any units or additional text.
- Do not include any explanatory text with your final answer, just the number itself.
"""