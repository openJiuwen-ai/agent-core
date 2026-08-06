#!/usr/bin/env python3
"""
Evaluation script for Table 5: Effectiveness of period reduction mechanism in PerStream
Tests NSBG and GSBN reduction strategies across multiple periods with passive mode
"""

import os
import json
import torch
import argparse
import numpy as np
from tqdm import tqdm
import time
from collections import defaultdict
import copy
import psutil

import warnings
import logging
# Suppress specific warning about system prompt
warnings.filterwarnings('ignore', message='.*System prompt modified.*')
warnings.filterwarnings('ignore', message='.*audio output may not work.*')
logging.getLogger().setLevel(logging.ERROR)  # Suppress WARNING level logs

from src.utils.model_utils import load_model
from src.utils.perstream_utils import get_triplet, get_text_embedding, generate_buffer_caption, get_image_embedding
from src.core.personalized_memory_graph import PersonalizedMemoryGraph
from src.core.memory_subcategories import get_memory_subclass_embeddings
from src.core.passive_user_query import passive_user_query
from src.core.memory_dataset_class import MemoryDataset
from concurrent.futures import ThreadPoolExecutor, as_completed


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--video_dir', required=True, help='Directory containing video files')
    parser.add_argument('--gt_file', required=True, help='Path to ground truth JSON file')
    parser.add_argument('--output_dir', required=True, help='Directory to save results')
    parser.add_argument('--output_name', default='period_reduction_results', help='Output file name')
    parser.add_argument('--model-path', type=str, required=True)
    parser.add_argument('--model-base', type=str, default=None)
    parser.add_argument('--api_key', type=str, default=None)
    parser.add_argument('--conv-mode', type=str, default='vicuna_v1')
    parser.add_argument('--image-size', type=int, default=224)
    parser.add_argument('--model-max-length', type=int, default=None)
    
    # Memory optimization arguments
    parser.add_argument("--low-memory", action="store_true", help="Enable low memory mode")
    parser.add_argument("--batch-size", type=int, default=1, help="Batch size for inference")
    parser.add_argument("--gradient-checkpointing", action="store_true", help="Enable gradient checkpointing")
    parser.add_argument("--cpu-offload", action="store_true", help="Offload model parts to CPU when possible")
    
    # Memory-specific arguments
    parser.add_argument("--no-include-memories", dest="include_memories", action="store_false", default=True, help="Disable memory context in prompts (default: memories are included)")
    parser.add_argument("--memory-types", nargs="+", default=["type_1_memories", "type_2_memories"],
                       help="Types of memories to include")
    parser.add_argument("--memory-format", type=str, default="structured", choices=["structured", "narrative"],
                       help="How to format memory context")
    parser.add_argument("--memory-strategy", type=str, default="graph-rag", 
                       choices=["none", "niah", "naive-rag", "m-rag", "graph-rag"],
                       help="Memory retrieval strategy")
    
    # Period settings
    parser.add_argument('--num_periods', type=int, default=4, help='Number of periods to evaluate')
    parser.add_argument('--device_ram_gb', type=float, default=4.0, 
                       help='Device CPU RAM capacity R (e.g., 4GB)')
    parser.add_argument('--r_prime_gb', type=float, default=None,
                       help="R' - Maximum CPU RAM usage per period (GB). If not provided, will be estimated empirically.")
    parser.add_argument('--num-chunks', type=int, default=1)
    parser.add_argument('--chunk_idx', type=int, default=0)
    
    # Video processing
    parser.add_argument('--max-frames', type=int, default=8)
    parser.add_argument('--target-fps', type=float, default=2)
    parser.add_argument('--video-start-before', type=float, default=2.0)
    parser.add_argument('--video-end-after', type=float, default=2.0)
    parser.add_argument('--video-extensions', nargs="+", default=[".mp4", ".avi", ".mov", ".mkv"])
    
    return parser.parse_args()


def calculate_metrics_with_gpt(predictions, ground_truths, questions, api_key):
    """
    Calculate evaluation metrics using GPT judge
    Returns: Accuracy (yes/no), Average Score (0-5)
    """
    from openai import OpenAI
    import ast

    client = OpenAI(api_key=api_key)
    
    scores = []
    yes_count = 0
    
    for pred, gt, question in zip(predictions, ground_truths, questions):
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": 
                        "You are an intelligent chatbot designed for evaluating the correctness of generative outputs for question-answer pairs. "
                        "Your task is to compare the predicted answer with the correct answer and determine if they match meaningfully. Here's how you can accomplish the task:"
                        "------"
                        "##INSTRUCTIONS: "
                        "- Focus on the meaningful match between the predicted answer and the correct answer.\n"
                        "- Consider synonyms or paraphrases as valid matches.\n"
                        "- Evaluate the correctness of the prediction compared to the answer."
                },
                {
                    "role": "user",
                    "content":
                        "Please evaluate the following video-based question-answer pair:\n\n"
                        f"Question: {question}\n"
                        f"Correct Answer: {gt}\n"
                        f"Predicted Answer: {pred}\n\n"
                        "Provide your evaluation only as a yes/no and score where the score is an integer value between 0 and 5, with 5 indicating the highest meaningful match. "
                        "Please generate the response in the form of a Python dictionary string with keys 'pred' and 'score', where value of 'pred' is a string of 'yes' or 'no' and value of 'score' is in INTEGER, not STRING."
                        "DO NOT PROVIDE ANY OTHER OUTPUT TEXT OR EXPLANATION. Only provide the Python dictionary string. "
                        "For example, your response should look like this: {'pred': 'yes', 'score': 4}."
                },
            ],
            temperature=0.002
        )
        
        response_message = completion.choices[0].message.content
        response_dict = ast.literal_eval(response_message)
        
        score = int(response_dict['score'])
        pred_response = response_dict['pred'].lower()
        print("Predictions:", predictions)
        print("Score:", score)
        print("Pred:", pred_response)
        
        scores.append(score)
        if 'yes' in pred_response:
            yes_count += 1
    
    metrics = {
        'A↑': round((yes_count / len(predictions)), 2) if predictions else 0.00,
        'S↑': round(np.mean(scores), 2) if scores else 0.00
    }
    
    return metrics


def process_memory_parallel(memory, api_key, memory_subclass_embedding_matrix, category_names, pmg):
    """Process a single memory with triplet extraction and PMG insertion"""
    try:
        triplets = get_triplet(memory, api_key=api_key)
        memory_vector = get_text_embedding(memory)
        
        # Get best matching category
        cosine_similarities = np.dot(memory_vector, memory_subclass_embedding_matrix.T) / (
            np.linalg.norm(memory_vector, axis=1, keepdims=True) * 
            np.linalg.norm(memory_subclass_embedding_matrix, axis=1)
        )
        cosine_similarities = cosine_similarities / np.linalg.norm(cosine_similarities, axis=1, keepdims=True)
        max_similarities_per_category = np.max(cosine_similarities, axis=0)
        best_match_idx = np.argmax(max_similarities_per_category)
        best_match_category = category_names[best_match_idx]
        
        for subject, predicate, obj in triplets:
            pmg.create(
                subject,
                predicate,
                obj,
                best_match_category,
                {
                    "caption_text": memory,
                    "caption_embedding": memory_vector
                }
            )
        return True
    except Exception as e:
        print(f"[ERROR] Memory processing failed: {e}")
        return False


def run_period_evaluation(args, model, processor, memory_subclass_embedding_matrix, 
                         category_names, samples, pmg, reduction_mode, period_num, user_id=None):
    """
    Run evaluation for one period with specified reduction mode.
    """
    predictions = []
    ground_truths = []
    latencies = []
    
    desc = f"User {user_id} - Period {period_num} - {reduction_mode}" if user_id else f"Period {period_num} - {reduction_mode}"
    
    # Build memory graph if not already built (only once per period)
    if period_num == 1:
        print(f"Building Memory Graph for user...")
        # Get all unique memories from all samples
        all_memories = []
        for sample in samples:
            all_memories.extend(sample['memories'])
        # Remove duplicates while preserving order
        seen = set()
        unique_memories = []
        for memory in all_memories:
            if memory not in seen:
                seen.add(memory)
                unique_memories.append(memory)
        
        max_workers = min(8, len(unique_memories))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    process_memory_parallel,
                    memory,
                    args.api_key,
                    memory_subclass_embedding_matrix,
                    category_names,
                    pmg
                )
                for memory in unique_memories
            ]
            for f in as_completed(futures):
                f.result()
    
    # Process each sample
    for sample in tqdm(samples, desc=desc):
        # Load video
        video_tensors = sample['video_tensor']
        question = sample['question']
        answer = sample['answer']
        
        # Inference
        start_time = time.time()
        with torch.inference_mode():
            # Generate caption for current buffer
            buffer_caption = generate_buffer_caption(video_tensors, model, processor)
            triplets = get_triplet(buffer_caption, api_key=args.api_key)
            buffer_caption_vector = get_text_embedding(buffer_caption)
            
            # Get category
            cosine_similarities = np.dot(buffer_caption_vector, memory_subclass_embedding_matrix.T) / (
                np.linalg.norm(buffer_caption_vector, axis=1, keepdims=True) * 
                np.linalg.norm(memory_subclass_embedding_matrix, axis=1)
            )
            cosine_similarities = cosine_similarities / np.linalg.norm(cosine_similarities, axis=1, keepdims=True)
            max_similarities_per_category = np.max(cosine_similarities, axis=0)
            best_match_idx = np.argmax(max_similarities_per_category)
            best_match_category = category_names[best_match_idx]
            
            # Extract frame embeddings
            frame_list = []
            for frame in video_tensors:
                frame_emb = get_image_embedding(frame, model, processor, for_storage=True, pool_size=(8,8))
                frame_list.append(frame_emb)
            
            # Add to PMG
            for triplet in triplets:
                subject, predicate, obj = triplet
                pmg.create(
                    subject,
                    predicate,
                    obj,
                    best_match_category,
                    {
                        "caption_text": buffer_caption,
                        "caption_embedding": buffer_caption_vector,
                        "mean_visual_vector": np.mean(frame_list, axis=0) if frame_list else None,
                        "nearest_visual_vector": frame_list[0] if frame_list else None
                    }
                )
            
            # Query in passive mode
            output = passive_user_query(
                model=model,
                processor=processor,
                pmg=pmg,
                query=question,
                short_term_memory_img_emb=frame_list
            )
        
        latency = time.time() - start_time
        
        predictions.append(output)
        ground_truths.append(answer)
        latencies.append(latency)
    
    # Calculate metrics
    questions = [s['question'] for s in samples]
    
    metrics = calculate_metrics_with_gpt(predictions, ground_truths, 
                                     questions, 
                                     args.api_key)
    metrics['avg_latency'] = round(np.mean(latencies), 2) if latencies else 0.00
    
    return metrics, pmg


def get_top_users_by_datapoints(samples, top_n=3):
    """
    Get top N users with most datapoints
    Returns: list of (participant_id, count) tuples
    """
    user_counts = defaultdict(int)
    for sample in samples:
        user_counts[sample['participant_id']] += 1
    
    # Sort by count descending
    sorted_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_users[:top_n]


def split_user_data_into_periods(user_samples, num_periods=4):
    """
    Split a single user's data equally into periods based on datapoint count.
    For example: 80 points -> first 20 is period 1, next 20 is period 2, etc.
    
    Args:
        user_samples: List of samples for one user (already sorted by timestamp)
        num_periods: Number of periods to split into (default 4)
    
    Returns:
        List of lists, where each sublist contains samples for that period
    """
    total_samples = len(user_samples)
    samples_per_period = total_samples // num_periods
    remainder = total_samples % num_periods
    
    period_samples = []
    start_idx = 0
    
    for period_idx in range(num_periods):
        # Distribute remainder samples to first few periods
        period_size = samples_per_period + (1 if period_idx < remainder else 0)
        end_idx = start_idx + period_size
        
        period_samples.append(user_samples[start_idx:end_idx])
        start_idx = end_idx
    
    return period_samples


def organize_samples_by_user_and_period(samples, num_periods=4):
    """
    Organize samples by user and period.
    1. Get top N users by datapoint count
    2. For each user, split their data equally into periods
    3. Return structure: {user_id: [period1_samples, period2_samples, ...]}
    """
    # Get top users
    top_users = get_top_users_by_datapoints(samples)
    top_user_ids = [user_id for user_id, _ in top_users]
    
    print(f"\nTop {len(top_users)} users by datapoint count:")
    for user_id, count in top_users:
        print(f"  User {user_id}: {count} datapoints ({count // num_periods} per period)")
    
    # Group samples by user
    user_samples_dict = defaultdict(list)
    for sample in samples:
        if sample['participant_id'] in top_user_ids:
            user_samples_dict[sample['participant_id']].append(sample)
    
    # Sort each user's samples by video_id and timestamp
    for user_id in user_samples_dict:
        user_samples_dict[user_id].sort(
            key=lambda x: (x['video_id'], float(x['timestamp']))
        )
    
    # Split each user's data into periods
    user_period_data = {}
    for user_id, user_samples in user_samples_dict.items():
        period_splits = split_user_data_into_periods(user_samples, num_periods)
        user_period_data[user_id] = period_splits
        
        print(f"\nUser {user_id} period distribution:")
        for i, period_samples in enumerate(period_splits, 1):
            print(f"  Period {i}: {len(period_samples)} samples")
    
    return user_period_data, top_users


def estimate_r_prime_empirically(period_samples, verbose=True):
    """
    Estimate R' (maximum CPU RAM usage over a period) empirically.
    Calculate based on memory vector sizes and number of nodes/edges.
    
    Args:
        period_samples: Samples from one period
        
    Returns:
        Estimated R' in GB
    """
    # Estimate memory per node based on storage granularities
    # M: text (~100 bytes)
    # v_M: text embedding (512 dims * 4 bytes = 2KB)
    # v̄: mean visual (8*8*512 * 4 bytes = 128KB)
    # v̂: nearest visual (8*8*512 * 4 bytes = 128KB)
    # Total per node ≈ 258KB
    
    bytes_per_node = (
        100 +  # M: text
        512 * 4 +  # v_M: text embedding (float32)
        8 * 8 * 512 * 4 +  # v̄: mean visual embedding
        8 * 8 * 512 * 4  # v̂: nearest visual embedding
    )
    
    # Estimate nodes from memories
    estimated_nodes = 0
    for sample in period_samples:
        memories = sample.get('type_1_memories', []) + sample.get('type_2_memories', [])
        # Assume ~2 nodes per memory (subject + object) with some deduplication
        estimated_nodes += len(memories) * 1.5
    
    # Add nodes from buffer captions (1 per sample)
    estimated_nodes += len(period_samples) * 2  # ~2 nodes per caption
    
    # Estimate edges (roughly same as nodes)
    estimated_edges = estimated_nodes
    
    # Edge memory: much smaller (~200 bytes per edge)
    bytes_per_edge = 200
    
    total_bytes = (estimated_nodes * bytes_per_node) + (estimated_edges * bytes_per_edge)
    total_gb = total_bytes / (1024**3)
    
    if verbose:
        print(f"  Estimated nodes: {int(estimated_nodes)}")
        print(f"  Estimated R': {total_gb:.2f} GB")
    
    return total_gb

def generate_table_5(results, output_dir):
    """
    Generate Table 5 format showing effectiveness of period reduction mechanism.
    Format: Period | w/o Reduction (A↑, S↑) | w/ NSBG (A↑, S↑) | w/ GSBN (A↑, S↑)
    """
    print("\n" + "="*80)
    print("TABLE 5: Effectiveness of Period Reduction Mechanism")
    print("="*80)
    
    # Group results by user and reduction mode
    user_results = defaultdict(lambda: defaultdict(list))
    for entry in results['periods']:
        user_id = entry['user_id']
        reduction_mode = entry['reduction_mode']
        user_results[user_id][reduction_mode] = entry['period_results']
    
    # Generate table for each user
    table_output = []
    table_output.append("\nConfig:")
    table_output.append(f"  Device RAM (R): {results['config']['device_ram_R']} GB")
    table_output.append(f"  Estimated R': {results['config']['estimated_R_prime']:.2f} GB")
    table_output.append(f"  Reduction triggered when: {results['config']['reduction_triggered_when']}")
    table_output.append("")
    
    for user_id in sorted(user_results.keys()):
        table_output.append(f"\n{'='*100}")
        table_output.append(f"User {user_id}")
        table_output.append(f"{'='*100}")
        
        # Header
        header = f"{'Period':<10} | {'w/o Reduction':<25} | {'w/ NSBG':<25} | {'w/ GSBN':<25}"
        table_output.append(header)
        table_output.append("-" * 100)
        
        # Get number of periods
        num_periods = results['config']['num_periods']
        
        # Organize data by period
        for period_num in range(1, num_periods + 1):
            row_data = {}
            
            for reduction_mode in ['w/o Reduction', 'w/ NSBG', 'w/ GSBN']:
                period_results = user_results[user_id][reduction_mode]
                
                # Find results for this period
                period_result = next(
                    (pr for pr in period_results if pr['period'] == period_num),
                    None
                )
                
                if period_result:
                    passive = period_result['passive']
                    a_score = passive['A↑']
                    s_score = passive['S↑']
                    row_data[reduction_mode] = f"A↑:{a_score:.2f} S↑:{s_score:.2f}"
                else:
                    row_data[reduction_mode] = "N/A"
            
            # Format row
            row = (f"{period_num:<10} | "
                   f"{row_data.get('w/o Reduction', 'N/A'):<25} | "
                   f"{row_data.get('w/ NSBG', 'N/A'):<25} | "
                   f"{row_data.get('w/ GSBN', 'N/A'):<25}")
            table_output.append(row)
        
        # Add summary statistics
        table_output.append("-" * 100)
        table_output.append("\nMemory Statistics:")
        
        for reduction_mode in ['w/o Reduction', 'w/ NSBG', 'w/ GSBN']:
            period_results = user_results[user_id][reduction_mode]
            
            if period_results:
                total_reductions = sum(1 for pr in period_results if pr.get('reduction_triggered', False))
                final_period = period_results[-1]
                final_nodes = final_period['pmg_nodes']
                final_edges = final_period['pmg_edges']
                final_ram = final_period['ram_after_gb']
                
                table_output.append(f"  {reduction_mode}:")
                table_output.append(f"    Reductions triggered: {total_reductions}")
                table_output.append(f"    Final PMG size: {final_nodes} nodes, {final_edges} edges")
                table_output.append(f"    Final RAM usage: {final_ram:.2f} GB")
        
        table_output.append("")
    
    # Calculate average metrics across all users
    table_output.append(f"\n{'='*100}")
    table_output.append("AVERAGE ACROSS ALL USERS")
    table_output.append(f"{'='*100}")
    
    avg_metrics = defaultdict(lambda: defaultdict(lambda: {'A': [], 'S': []}))
    
    for entry in results['periods']:
        reduction_mode = entry['reduction_mode']
        for pr in entry['period_results']:
            period = pr['period']
            avg_metrics[period][reduction_mode]['A'].append(pr['passive']['A↑'])
            avg_metrics[period][reduction_mode]['S'].append(pr['passive']['S↑'])
    
    # Header
    header = f"{'Period':<10} | {'w/o Reduction':<25} | {'w/ NSBG':<25} | {'w/ GSBN':<25}"
    table_output.append(header)
    table_output.append("-" * 100)
    
    for period_num in range(1, results['config']['num_periods'] + 1):
        row_data = {}
        
        for reduction_mode in ['w/o Reduction', 'w/ NSBG', 'w/ GSBN']:
            if reduction_mode in avg_metrics[period_num]:
                a_vals = avg_metrics[period_num][reduction_mode]['A']
                s_vals = avg_metrics[period_num][reduction_mode]['S']
                
                if a_vals and s_vals:
                    avg_a = round(np.mean(a_vals), 2)
                    avg_s = round(np.mean(s_vals), 2)
                    row_data[reduction_mode] = f"A↑:{avg_a:.2f} S↑:{avg_s:.2f}"
                else:
                    row_data[reduction_mode] = "N/A"
            else:
                row_data[reduction_mode] = "N/A"
        
        row = (f"{period_num:<10} | "
               f"{row_data.get('w/o Reduction', 'N/A'):<25} | "
               f"{row_data.get('w/ NSBG', 'N/A'):<25} | "
               f"{row_data.get('w/ GSBN', 'N/A'):<25}")
        table_output.append(row)
    
    # Print and save
    table_text = "\n".join(table_output)
    print(table_text)
    
    # Save to file
    table_path = os.path.join(output_dir, "table_5.txt")
    with open(table_path, 'w') as f:
        f.write(table_text)
    
    print(f"\nTable 5 saved to: {table_path}")

def run_experiment(args):
    """Main experiment runner"""
    
    # Set memory optimization flags
    if args.low_memory:
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    
    # Clear cache before starting
    
    
    # Initialize model
    print("Loading model...")
    model, processor = load_model(args.model_path, device='cuda')
    
    # Move model to half precision to save memory if low_memory mode
    if args.low_memory:
        model = model.half()
    
    print("Generating memory subclass embeddings...")
    memory_subclass_embedding_matrix, category_names = get_memory_subclass_embeddings(model, processor)
    
    
    
    # Load data
    with open(args.gt_file) as f:
        test_samples = json.load(f)
    
    print(f"Total test samples: {len(test_samples)}")
    
    # Get top 5 users and organize by period
    user_period_data, top_users = organize_samples_by_user_and_period(
        test_samples, 
        num_periods=args.num_periods
    )
    
    # Estimate R (device CPU RAM capacity)
    R = args.device_ram_gb  # e.g., 128GB
    print(f"\nDevice RAM (R): {R} GB")
    
    # Get R' (maximum RAM usage per period)
    if args.r_prime_gb is not None:
        R_prime = args.r_prime_gb
        print(f"R' (max usage per period): {R_prime:.2f} GB (from argument)")
    else:
        # Estimate R' for first period (maximal usage over a period)
        # We'll use the first user's first period as representative
        first_user_id = top_users[0][0]
        first_period_samples = user_period_data[first_user_id][0]
        R_prime = estimate_r_prime_empirically(first_period_samples)
        print(f"Estimated R' (max usage per period): {R_prime:.2f} GB")
    
    # Results storage
    results = {
        'periods': [],
        'config': {
            'num_periods': args.num_periods,
            'top_users': [{'user_id': uid, 'datapoints': count} for uid, count in top_users],
            'device_ram_R': R,
            'estimated_R_prime': R_prime,
            'reduction_triggered_when': f"R - R_cur < R' (i.e., {R} - R_cur < {R_prime:.2f})"
        }
    }
    
    # Process each user separately
    for user_id, datapoint_count in top_users:
        print(f"\n{'='*80}")
        print(f"Processing User {user_id} ({datapoint_count} total datapoints)")
        print(f"{'='*80}")
        
        user_periods = user_period_data[user_id]
        
        # Evaluate each reduction strategy for this user
        for reduction_mode in ['NSBG', 'GSBN', None]:
            reduction_name = f"w/ {reduction_mode}" if reduction_mode else "w/o Reduction"
            print(f"\n{'-'*60}")
            print(f"Strategy: {reduction_name}")
            print(f"{'-'*60}")
            
            # Initialize fresh PMG for this user + strategy
            # Allow duplicates to maximize nodes and edges for this experiment
            pmg = PersonalizedMemoryGraph(get_text_embedding, similarity_threshold=1, allow_duplicates=True)
            
            user_period_results = []
            
            for period_num in range(1, args.num_periods + 1):
                print(f"\nPeriod {period_num}/{args.num_periods}")
                
                period_samples = user_periods[period_num - 1]
                print(f"  Samples in period: {len(period_samples)}")
                
                # Create dataset for this period
                dataset = MemoryDataset(period_samples, args.video_dir, processor, model.config, args)
                
                # Check if reduction needed (before processing new period)
                # Measure actual RAM usage
                process = psutil.Process(os.getpid())
                current_ram_gb = process.memory_info().rss / (1024**3)
                
                R_cur = current_ram_gb
                available = R - R_cur
                
                print(f"  Current RAM: {R_cur:.2f} GB")
                print(f"  Available RAM: {available:.2f} GB")
                print(f"  R' threshold: {R_prime:.2f} GB")
                
                reduction_triggered = False
                if reduction_mode and period_num > 1 and R_cur + R_prime > R:
                    reduction_triggered = True
                    target_reduction = (R_cur + R_prime) - R  # Free enough for new period + buffer
                    print(f"  [WARNING]  Reduction TRIGGERED: Need to free {target_reduction:.2f} GB")
                    
                    reduction_stats = pmg.reduce(
                        target_memory_mb=target_reduction * 1024,
                        reduction_mode=reduction_mode
                    )
                    print(f"  Freed: {reduction_stats['total_memory_freed_mb']:.2f} MB")
                    print(f"  Nodes modified: {reduction_stats['nodes_modified']}")
                else:
                    print(f"  [OK] No reduction needed")
                
                # Evaluate passive mode
                passive_metrics, pmg = run_period_evaluation(
                    args, model, processor, memory_subclass_embedding_matrix,
                    category_names, list(dataset), pmg, reduction_name, 
                    period_num, user_id=user_id
                )
                
                # Track RAM after period
                process = psutil.Process(os.getpid())
                current_ram_after_gb = process.memory_info().rss / (1024**3)
                
                R_cur_after = current_ram_after_gb
                ram_growth = R_cur_after - R_cur
                
                period_result = {
                    'user_id': user_id,
                    'period': period_num,
                    'reduction_mode': reduction_name,
                    'reduction_triggered': reduction_triggered,
                    'passive': passive_metrics,
                    'ram_before_gb': R_cur,
                    'ram_after_gb': R_cur_after,
                    'ram_growth_gb': ram_growth,
                    'pmg_nodes': len(pmg.nodes),
                    'pmg_edges': len(pmg.edges)
                }
                
                user_period_results.append(period_result)
                
                # Print summary
                print(f"\n  Results:")
                print(f"    Passive - A↑: {passive_metrics['A↑']:.2f}, S↑: {passive_metrics['S↑']:.2f}")
                print(f"    RAM growth: {ram_growth:.2f} GB")
                print(f"    PMG size: {len(pmg.nodes)} nodes, {len(pmg.edges)} edges")
            
            results['periods'].append({
                'user_id': user_id,
                'reduction_mode': reduction_name,
                'period_results': user_period_results
            })
    
    # Save results
    output_path = os.path.join(args.output_dir, f"{args.output_name}.json")
    os.makedirs(args.output_dir, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Generate Table 5 format
    generate_table_5(results, args.output_dir)
    
    print(f"\n{'='*80}")
    print(f"Results saved to {output_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    args = parse_args()
    run_experiment(args)