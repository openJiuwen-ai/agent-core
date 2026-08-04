# This file may have been modified by Flash-VStream Authors (Flash-VStream Modifications"). All Flash-VStream Modifications are Copyright 2024 Flash-VStream Authors.
# Based on https://github.com/haotian-liu/LLaVA.
import os
import json
import math
import torch
import random
import argparse
import numpy as np
from tqdm import tqdm
import time
from PIL import Image
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.utils.model_utils import load_model
from src.utils.perstream_utils import get_triplet, get_text_embedding, generate_buffer_caption, get_image_embedding
from src.core.personalized_memory_graph import PersonalizedMemoryGraph
from src.core.memory_subcategories import get_memory_subclass_embeddings
from src.core.passive_user_query import passive_user_query
from src.core.memory_dataset_class import MemoryDataset

def parse_args():
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser()
    # Define the command-line arguments
    parser.add_argument('--video_dir', help='Directory containing video files.', required=True)
    parser.add_argument('--gt_file', help='Path to the ground truth file containing question.', required=True)
    parser.add_argument('--output_dir', help='Directory to save the model results JSON.', required=True)
    parser.add_argument('--output_name', help='Name of the file for storing results JSON.', required=True)
    parser.add_argument("--model-path", type=str, default="facebook/opt-350m")
    parser.add_argument("--model-base", type=str, default=None)
    parser.add_argument("--conv-mode", type=str, default="vicuna_v1")
    parser.add_argument("--num-chunks", type=int, default=1)
    parser.add_argument("--chunk_idx", type=int, default=0)
    parser.add_argument("--model-max-length", type=int, default=None)
    
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
    parser.add_argument("--memory-strategy", type=str, default="none", choices=["none", "niah", "naive-rag", "m-rag", "graph-rag"],
                       help="How to format memory context")
    
    # Video processing arguments (optimized defaults)
    parser.add_argument("--max-frames", type=int, default=8, help="Maximum frames to extract per video (reduced for memory)")
    parser.add_argument("--image-size", type=int, default=224, help="Image size for video frames")
    parser.add_argument("--video-extensions", nargs="+", default=[".mp4", ".avi", ".mov", ".mkv"],
                       help="Video file extensions to look for")
    parser.add_argument("--video-start-before", type=float, default=2.0, help="Seconds to extract before timestamp")
    parser.add_argument("--video-end-after", type=float, default=2.0, help="Seconds to extract after timestamp")
    parser.add_argument("--target-fps", type=float, default=1, help="Target FPS for frame sampling (reduced for memory)")
    parser.add_argument("--api_key", type=str, default=None, help="Add key here: sk-proj...")

    parser.add_argument("--rho", type=float, default=0.6, help="Rho PMG")
    parser.add_argument("--delta", type=float, default=0.6, help="Delta PMG")
    parser.add_argument("--topk", type=int, default=5, help="Top-K PMG")

    return parser.parse_args()

def get_best_match_category(vector, memory_subclass_embedding_matrix, category_names):
    cosine_similarities = np.dot(vector, memory_subclass_embedding_matrix.T) / (
        np.linalg.norm(vector, axis=1, keepdims=True) * 
        np.linalg.norm(memory_subclass_embedding_matrix, axis=1)
    )
    # normalize cosine for each patch
    cosine_similarities = cosine_similarities / np.linalg.norm(cosine_similarities, axis=1, keepdims=True)
    
    # Take the maximum similarity across all patches for each category
    # Shape: [num_categories]
    max_similarities_per_category = np.max(cosine_similarities, axis=0)
    
    # Find the category with highest similarity
    best_match_idx = np.argmax(max_similarities_per_category)
    max_similarity = max_similarities_per_category[best_match_idx]
    best_match_category = category_names[best_match_idx]
    return best_match_category

def get_model_name_from_path(model_path):
    model_path = model_path.strip("/")
    model_paths = model_path.split("/")
    if model_paths[-1].startswith('checkpoint-'):
        return model_paths[-2] + "_" + model_paths[-1]
    else:
        return model_paths[-1]

def process_memory(memory, api_key, memory_subclass_embedding_matrix, category_names, pmg):
    try:
        triplets = get_triplet(memory, api_key=api_key)
        memory_vector = get_text_embedding(memory)
        best_match_category = get_best_match_category(memory_vector, memory_subclass_embedding_matrix, category_names)
        
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
    except Exception as e:
        print(f"[ERROR] Memory '{memory}' failed with: {e}")

def run_inference(args):
    """
    Run inference on Memory-based Video QA Dataset using the Video-ChatGPT model.
    Args:
        args: Command-line arguments.
    """
    # Set memory optimization flags
    if args.low_memory:
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    
    model_name = get_model_name_from_path(args.model_path)
    if "finetune" in model_name:
        model_name += "_lora"
    
    print("Loading model...")
    model, processor = load_model(args.model_path)

    print("Generating memory subclass embeddings...")
    memory_subclass_embedding_matrix, category_names = get_memory_subclass_embeddings(model, processor)
    
    # Move model to half precision to save memory
    if args.low_memory:
        model = model.half()
    
    print("Model loaded successfully")

    # Load ground truth file containing questions and answers
    with open(args.gt_file) as file:
        all_questions = json.load(file)
    
    print(f"Loaded {len(all_questions)} total samples")

    # filter passive mode
    all_questions = [q for q in all_questions if q.get('type') == 'passive']
    gt_questions = [q for q in all_questions if q.get('split') == 'test']
        
    # Ensure each sample has a unique ID
    for i, sample in enumerate(gt_questions):
        if 'id' not in sample:
            video_id = sample.get('video_id', 'unknown')
            timestamp = sample.get('timestamp', '0')
            sample['id'] = f"{video_id}_{timestamp}_{i}"

    # Create the output directory if it doesn't exist
    if not os.path.exists(args.output_dir):
        try:
            os.makedirs(args.output_dir)
        except Exception as e:
            print(f'mkdir Except: {e}')

    output_name = args.output_name
    answers_file = os.path.join(args.output_dir, f"{output_name}.json")
    
    # Resume from old experiment
    exist_id_set = set()
    if os.path.exists(answers_file):
        with open(answers_file) as f:
            exist_pred_contents = [json.loads(line) for line in f]
        exist_id_set = set([x['id'] for x in exist_pred_contents])

    new_gt_questions = []
    for sample in tqdm(gt_questions):
        if sample['id'] not in exist_id_set:
            new_gt_questions.append(sample)
    
    gt_questions = new_gt_questions[:len(new_gt_questions)] # sample 1/10 dataset to reduce time
    print(f"Processing {len(gt_questions)} samples with batch size {args.batch_size}")

    dataset = MemoryDataset(gt_questions, args.video_dir, processor, model.config, args)

    with open(answers_file, "a") as ans_file:
        for batch in tqdm(dataset, desc=f"cuda:{args.chunk_idx}", total=len(dataset)):    
            video_tensors = batch['video_tensor']
            video_ids = batch['video_id']
            questions = batch['question']
            answers = batch['answer']
            memories = batch['memories']

            pmg = PersonalizedMemoryGraph(get_text_embedding, similarity_threshold=args.rho, delta_dfs_threshold=args.delta)
            print("Building Memory Graph...")
            max_workers = min(8, len(memories)) 
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(
                        process_memory,
                        memory,
                        args.api_key,
                        memory_subclass_embedding_matrix,
                        category_names,
                        pmg
                    )
                    for memory in memories
                ]

            print("Start Inference...")
            with torch.inference_mode():
                # -----------------------------
                # 1. Generate caption
                # -----------------------------
                buffer_caption = generate_buffer_caption(video_tensors, model, processor)

                # -----------------------------
                # 2. Triplet extraction
                # -----------------------------
                triplets = get_triplet(buffer_caption, api_key=args.api_key)

                # -----------------------------
                # 3. Caption embedding
                # -----------------------------
                buffer_caption_vector = get_text_embedding(buffer_caption)

                # -----------------------------
                # 4. Category matching
                # -----------------------------
                best_match_category = get_best_match_category(
                    buffer_caption_vector,
                    memory_subclass_embedding_matrix,
                    category_names
                )

                # -----------------------------
                # 5. Frame embedding
                # -----------------------------
                frame_list = []
                for frame in video_tensors:
                    frame_emb = get_image_embedding(frame, model, processor, for_storage=True, pool_size=(8,8))
                    frame_list.append(frame_emb)

                # -----------------------------
                # 6. PMG triplet creation
                # -----------------------------
                for triplet in triplets:
                    subject, predicate, obj = triplet
                    subj_id, edge_id, obj_id = pmg.create(
                        subject,
                        predicate,
                        obj,
                        best_match_category,
                        {
                            "caption_text": buffer_caption,
                            "caption_embedding": buffer_caption_vector
                        }
                    )
                
                # -----------------------------
                # 7. Passive user query
                # -----------------------------
                start_time = time.time()
                outputs, retrieval_time = passive_user_query(
                    model=model, 
                    processor=processor, 
                    pmg=pmg, 
                    query=questions, 
                    short_term_memory_img_emb=frame_list,
                    output_retrieval_time=True,
                    top_k=args.topk
                )
                
                # -----------------------------
                # Total time
                # -----------------------------
                latency = time.time() - start_time
                allocated_gb = torch.cuda.memory_allocated() / 1024**3
                
                sample_set = {
                    'id': video_ids,
                    'question': questions,
                    'answer': answers,
                    'pred': outputs,
                    'latency': latency,
                    'vram': allocated_gb,
                    '#nodes': len(pmg.nodes),
                }
                
                ans_file.write(json.dumps(sample_set) + "\n")
                
                ans_file.flush()
                


if __name__ == "__main__":
    args = parse_args()
    
    # Print memory optimization settings
    print(f"Batch size: {args.batch_size}")
    if args.low_memory:
        print("Low memory mode enabled:")
        print(f"  - Max frames: {args.max_frames}")
        print(f"  - Target FPS: {args.target_fps}")
        print(f"  - Gradient checkpointing: {args.gradient_checkpointing}")
    
    run_inference(args)