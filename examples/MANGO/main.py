def main():
    import os

    os.environ["OPENAI_API_KEY"] = "your_key"
    os.environ["OPENAI_API_BASE"] = "your_url"

    import sys
    # root_dir = "your_path"
    # sys.path.append(root_dir)
    import warnings
    warnings.filterwarnings('ignore')
    from sentence_transformers import SentenceTransformer
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id="sentence-transformers/all-MiniLM-L6-v2",
        local_dir="./hugging_face/all-MiniLM-L6-v2",
        local_dir_use_symlinks=False,
        # local_files_only=True,
        resume_download= True,
        force_download= False
    )
    embed_model = SentenceTransformer("./hugging_face/all-MiniLM-L6-v2")
    
    import asyncio
    import argparse
    from utilities import build_graph, get_dataset_info
    from training_tg import Training_RL, Training_RL_TG, Evaluation_RL_TG, Evaluation_Test, save_system_prompt, load_system_prompt
    from human_eval.data import write_jsonl
    from PolicyGradient import PolicyGradient, RL_Environment
    import time
    from datetime import datetime
    from token_usage import TOKEN_USAGE

    # from mango_benchmark.benchmark import BaseBenchmark
    from mango_benchmark.gsm8k import GSM8KBenchmark
    from mango_benchmark.humaneval import HUMANEVALBenchmark
    from mango_benchmark.math import MATHBenchmark
    from mango_benchmark.drop import DROPBenchmark
    from mango_benchmark.gpqa import GPQABenchmark
    from mango_benchmark.mmlu import MMLUBenchmark
    from mango_benchmark.mbpp import MBPPBenchmark
    
    def parse_args():
        parser = argparse.ArgumentParser(description="MANGO Optimizer")
        parser.add_argument("--train_benchmark_selected", type=str, default="math", help="the benchmark selected")
        parser.add_argument("--test_benchmark_selected", type=str, default="math", help="the test benchmark selected")
        parser.add_argument("--model", type=str, default="gpt-4o-mini", help="the selected model")
        parser.add_argument("--data_train_percent", type=float, default=0.8, help="dataset partition percent")
        parser.add_argument("--threshold", type=float, default=0.7, help="the benchmark selected")
        parser.add_argument("--concurrency", type=int, default=30, help="async concurrency")
        parser.add_argument("--num_rl_episodes", type=float, default=75, help="rl episode")
        parser.add_argument("--num_tg_episodes", type=float, default=5, help="tg episode")
        parser.add_argument("--result_dir", type=str, default="ckpt/", help="tg episode")
        return parser.parse_args()
    
    args = parse_args()
    dataset_configs = {
        "gsm8k": GSM8KBenchmark,
        "math": MATHBenchmark,
        "humaneval": HUMANEVALBenchmark,
        "drop": DROPBenchmark,
        "gpqa": GPQABenchmark,
        "mmlu": MMLUBenchmark,
        "mbpp": MBPPBenchmark
    }
    train_benchmark_selected = args.train_benchmark_selected
    test_benchmark_selected = args.test_benchmark_selected
    
    # train_data_path = f"./dataset/{train_benchmark_selected.upper()}/validate.jsonl"
    # test_data_path = f"./dataset/{test_benchmark_selected.upper()}/test.jsonl"
    train_data_path = f"./dataset/{train_benchmark_selected.upper()}/validate_try.jsonl"
    test_data_path = f"./dataset/{test_benchmark_selected.upper()}/test_try.jsonl"
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d-%H-%M-%S")
    record_dir = f"dataset/{train_benchmark_selected.upper()}/test/{date_str}"
    os.makedirs(record_dir, exist_ok=True)
    
    train_benchmark = dataset_configs[train_benchmark_selected](train_benchmark_selected, train_data_path, record_dir, "train", embed_model)
    test_benchmark = dataset_configs[test_benchmark_selected](test_benchmark_selected, test_data_path, record_dir, "test", embed_model)
    
#################################################################
# Create graph                                              #
#################################################################

    with open(f"{record_dir}/log.txt", "w", encoding="utf-8") as f:
        print(f"Train dataset: {train_data_path}", file = f, flush=True)
        print(f"Test dataset: {test_data_path}", file = f, flush=True)
        threshold = args.threshold
        concurrency = args.concurrency
        print("Building graph starts")
        token_usage = TOKEN_USAGE
        
        model_string = args.model
        G, node_to_vecs, node_to_rd_vecs, query_to_edge, starting_nodes, tid_to_path = build_graph(f, train_benchmark, embed_model, threshold)
        print(f"Number of nodes: {len(G.nodes)}", file=f, flush=True)

        n_features = 4  # Number of features in the observation 
        n_hiddens = 128
        # num_rl_episodes = 75
        # num_tg_episodes = 5
        num_rl_episodes = 1
        num_tg_episodes = 1

        #################################################################
        # Create RL Agent                                              #
        #################################################################
        RL_Agent = PolicyGradient(n_features = n_features, n_hiddens = n_hiddens, learning_rate = 0.001, gamma = 0.95)
        env = RL_Environment(G, node_to_rd_vecs, node_to_vecs)

        train_workflows, valid_workflows = train_benchmark.train_workflows, train_benchmark.valid_workflows
        for i in range(len(train_workflows)):
            for j in range(len(train_workflows[i])):
                train_workflows[i][j], _ = get_dataset_info(train_workflows[i][j])
                train_workflows[i][j], _ = train_workflows[i][j].split("|", 1)
                train_workflows[i][j] = train_workflows[i][j].strip()
        for i in range(len(valid_workflows)):
            for j in range(len(valid_workflows[i])):
                valid_workflows[i][j], _ = get_dataset_info(valid_workflows[i][j])
                valid_workflows[i][j], _ = valid_workflows[i][j].split("|", 1)
                valid_workflows[i][j] = valid_workflows[i][j].strip()
        ##################################################################
        # Training                                      #
        ##################################################################
        result_dir = "ckpt/"

        print("Training_RL starts")
        train_start_time = time.time()
        # best_score = -1
        for episode in range(num_rl_episodes):
            
            print(f"\n[TRAININGPG] Training episode = {episode+1}", file=f, flush=True)
            Training_RL(f, G, env, RL_Agent, train_benchmark, tid_to_path)

        RL_dir = result_dir + 'train_' + f"{time.time():.4f}"
        os.makedirs(RL_dir, exist_ok=True)
        RL_Agent.save(RL_dir + '/test.pth')
        print("Training_RL_TG starts")
        
        starting_nodes_to_vecs = {}
        for starting_node in starting_nodes:
            starting_nodes_to_vecs[starting_node] = G.nodes[starting_node]["updated_system_prompt_vector"]
        # Original System Prompt Accuracy
        best_score, results = asyncio.run(Evaluation_RL_TG(f, G, env, RL_Agent, concurrency, starting_nodes_to_vecs, train_benchmark, model_string))
        print(f"original system prompt, score: {best_score}", file=f, flush=True)
        train_dir = record_dir + '/original_RL_TG_' + f"{best_score:.4f}"
        os.makedirs(train_dir, exist_ok=True)
        RL_Agent.save(train_dir + '/test.pth')
        save_system_prompt(G, train_dir)
        write_jsonl(f"{train_dir}/Training_Ans.jsonl", results)
        
        for episode in range(num_tg_episodes):
            
            asyncio.run(Training_RL_TG(f, G, env, RL_Agent, episode, concurrency, train_benchmark, tid_to_path, model_string))
            
            starting_nodes_to_vecs = {}
            for starting_node in starting_nodes:
                starting_nodes_to_vecs[starting_node] = G.nodes[starting_node]["updated_system_prompt_vector"]
                
            score, results = asyncio.run(Evaluation_RL_TG(f, G, env, RL_Agent, concurrency, starting_nodes_to_vecs, train_benchmark, model_string))
            print(f"\n[TRAININGPG] episode_{episode+1}_score: {score}", file=f, flush=True)
            if score >= best_score or score==1.0:
                best_score = score
                train_dir = record_dir + '/train_RL_TG_' + f"{best_score:.4f}_episode_{episode+1}"
                os.makedirs(train_dir, exist_ok=True)
                RL_Agent.save(train_dir + '/test.pth')
                save_system_prompt(G, train_dir)
                write_jsonl(f"{train_dir}/Training_Ans.jsonl", results)
                
        print(f"\n[TRAININGPG] train_rl_tg_score: {best_score} total_train_time: {time.time() - train_start_time}", file=f, flush=True)
        
        token_summary = token_usage.get_usage()
        train_input_tokens, train_output_tokens, train_cost = token_summary["total_input_tokens"], token_summary["total_output_tokens"], token_summary["total_cost"]
        print(f"\n[TRAININGPG] input_token: {train_input_tokens} output_token: {train_output_tokens} total_cost: {train_cost}", file=f, flush=True)
        
        print("Testing starts")
        
        # model_string = "qwen/qwen-2.5-72b-instruct"
        # model_string = "meta-llama/llama-3.1-70b-instruct"
        # G.nodes[0]['planner_model'].engine.model_string = model_string
        # G.nodes[1]['executor_model'].engine.model_string = model_string
        RL_Agent.load(train_dir + '/test.pth')
        load_system_prompt(G, train_dir)
        
        starting_nodes_to_vecs = {}
        for starting_node in starting_nodes:
            # starting_nodes_to_vecs[starting_node] = node_to_vecs[starting_node]
            starting_nodes_to_vecs[starting_node] = G.nodes[starting_node]["updated_system_prompt_vector"]
        
        test_score, test_time = asyncio.run(Evaluation_Test(f, record_dir, G, env, RL_Agent, concurrency, starting_nodes_to_vecs, test_benchmark, model_string))

        print(f"\n[TEST] test_score: {test_score} test_time: {test_time}", file=f, flush=True)
        print(f"\n[TEST] test_score: {test_score} test_time: {test_time}")
        
        token_summary = token_usage.get_usage()
        total_input_tokens, total_output_tokens, total_cost = token_summary["total_input_tokens"], token_summary["total_output_tokens"], token_summary["total_cost"]
        
        print(f"\n[Test] input_token: {total_input_tokens-train_input_tokens} output_token: {total_output_tokens-train_output_tokens} total_cost: {total_cost-train_cost}", file=f, flush=True)
        print(f"\n[Test] input_token: {total_input_tokens-train_input_tokens} output_token: {total_output_tokens-train_output_tokens} total_cost: {total_cost-train_cost}")
        print(f"\n[TOTAL] input_token: {total_input_tokens} output_token: {total_output_tokens} total_cost: {total_cost}", file=f, flush=True)
        print(f"\n[TOTAL] input_token: {total_input_tokens} output_token: {total_output_tokens} total_cost: {total_cost}")
        
if __name__ == "__main__":
    main()