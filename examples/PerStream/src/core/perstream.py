#!/usr/bin/env python3
"""
Multi-GPU Video and Text Embedding with Qwen2.5-Omni
Shards model across 2 GPUs to handle memory constraints
"""

import torch
import numpy as np
import time
import argparse
from collections import deque

from src.utils.perstream_utils import get_triplet, get_text_embedding
from src.utils.video_utils import process_video
from src.core.memory_subcategories import get_memory_subclass_embeddings
from src.core.personalized_memory_graph import PersonalizedMemoryGraph
from src.core.passive_user_query import passive_user_query
from src.core.proactive_user_query import proactive_user_query
from src.utils.perstream_utils import get_image_embedding, remember_gate, generate_buffer_caption
from src.utils.model_utils import load_projection_model, load_model

# Suppress the specific Qwen audio warning
import logging
import warnings
logging.getLogger().setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message="System prompt modified*")

def video_stream_with_memory(queries, video_path, model_path, projection_model_path, buffer_size=8, queue_size=8, gamma_threshold=0.5, api_key=None, pool_size=(8, 8), enable_proactive=True):
    """
    Extract embeddings with model and support both passive and proactive modes.

    Args:
        queries: List of user queries for passive mode
        video_path: Path to video file
        model_path: Path to model
        buffer_size: Number of frames per buffer
        queue_size: Size of short-term memory queue
        gamma_threshold: Similarity threshold for memory gate
        api_key: OpenAI API key for triplet extraction
        pool_size: Tuple (H, W) for pooling dimensions
        enable_proactive: If True, trigger proactive responses after each buffer
    """

    # get video id
    video_id = video_path.split("/")[-1].split(".")[0]

    # Initialize multi-GPU model manager
    model, processor = load_model(model_path)
    # Load model across multiple GPUs
    projection_mlp = load_projection_model(projection_model_path) 
    projection_mlp = projection_mlp.to(model.device)
    
    pmg = PersonalizedMemoryGraph(get_text_embedding)
    
    print("Generating memory subclass embeddings...")
    memory_subclass_embedding_matrix, category_names = get_memory_subclass_embeddings(model, processor)
    all_frames, selected_ids = process_video(video_path, sample_rate=4)

    # Apply selective retention with buffer
    buffer_frames = []
    buffer_embs = []
    frame_similarities = []
    qna_pairs = []

    idx = 0
    short_term_memory_img_emb = deque([], maxlen=queue_size)
    while idx < len(all_frames):
        frame_idx = selected_ids[idx]
        print(f"Processing frame {frame_idx}")
        frame = all_frames[idx]
        if len(buffer_frames) >= buffer_size:
            break

        # Apply remember gate based on memory subclass similarity
        should_remember, max_similarity, best_match_category, frame_embedding_pooled = remember_gate(
            frame, memory_subclass_embedding_matrix, category_names, model, processor, projection_mlp, gamma_threshold=gamma_threshold, pool_size=pool_size
        )

        print(f"Frame {frame_idx}: similarity={max_similarity:.3f}, match={best_match_category}, remember={should_remember}")
        frame_similarities.append(max_similarity)
        short_term_memory_img_emb.append(frame_embedding_pooled)  # Store pooled embeddings for visual context

        if should_remember:
            # Remember this frame and extract detailed memory info
            buffer_frames.append(frame)
            # Store pooled embeddings
            buffer_embs.append(frame_embedding_pooled)

            print(f"Frame {frame_idx} remembered - Category: {best_match_category}, Similarity: {max_similarity:.3f}")

            # Add similar frames directly without gate check
            while len(buffer_frames) < buffer_size and idx + 1 < len(all_frames):
                idx += 1
                if idx < len(all_frames):
                    buffer_frames.append(all_frames[idx])
                    pooled_emb = get_image_embedding(all_frames[idx], model, processor, for_storage=True, pool_size=pool_size)
                    buffer_embs.append(pooled_emb)

            # Extract pooled embeddings for storage
            pooled_embeddings = buffer_embs if buffer_embs else []

            # Stack pooled embeddings for similarity calculations
            # Each pooled_emb has shape [pool_h*pool_w, hidden_dim]
            video_embedding_pooled = np.stack(pooled_embeddings, axis=0) if pooled_embeddings else np.array([])

            # Calculate average vector of all frame pooled embeddings  
            # Average across temporal dimension (frames) while preserving spatial patches
            # Shape: (num_frames, pool_h*pool_w, hidden_dim) -> (pool_h*pool_w, hidden_dim)
            average_vector_pooled = np.mean(video_embedding_pooled, axis=0) if len(pooled_embeddings) > 0 else np.array([])

            # Find frame with highest cosine similarity to average vector
            max_sim = -1
            best_frame_vector_pooled = None

            if len(pooled_embeddings) > 0:
                # Compute patch-wise cosine similarity between each frame and average
                # video_embedding_pooled: (num_frames, num_patches, hidden_dim)
                # average_vector_pooled: (num_patches, hidden_dim)
                
                # Normalize vectors for cosine similarity
                frame_norms = np.linalg.norm(video_embedding_pooled, axis=2, keepdims=True)  # (num_frames, num_patches, 1)
                avg_norms = np.linalg.norm(average_vector_pooled, axis=1, keepdims=True)  # (num_patches, 1)
                
                # Compute cosine similarity for each patch across all frames
                # Shape: (num_frames, num_patches)
                patch_similarities = np.sum(video_embedding_pooled * average_vector_pooled[np.newaxis, :, :], axis=2) / (
                    frame_norms.squeeze(-1) * avg_norms.squeeze(-1)
                )
                
                # Aggregate patch similarities to get overall frame similarity (mean across patches)
                frame_similarities = np.mean(patch_similarities, axis=1)  # (num_frames,)
                
                best_frame_idx = np.argmax(frame_similarities)
                max_sim = frame_similarities[best_frame_idx]
                best_frame_vector_pooled = video_embedding_pooled[best_frame_idx]

            # Generate buffer caption using multi-GPU model
            if buffer_frames:
                buffer_caption = generate_buffer_caption(buffer_frames, model, processor)
                buffer_caption_vector = get_text_embedding(buffer_caption)
            else:
                buffer_caption = "No frames selected for caption generation"
                buffer_caption_vector = np.array([])


            # Pass the api_key to get_triplet function
            triplets = get_triplet(buffer_caption, api_key=api_key, verbose=True)
            print(f"Triplets: {triplets}")

            for triplet in triplets:
                subject, predicate, obj = triplet
                # Store pooled embeddings in the graph (for later retrieval with matched tokens)
                subj_id, edge_id, obj_id = pmg.create(
                    subject,
                    predicate,
                    obj,
                    best_match_category,
                    {
                        "caption_text": buffer_caption,
                        "caption_embedding": buffer_caption_vector,
                        "mean_visual_vector": average_vector_pooled,  # Pooled features for storage
                        "nearest_visual_vector": best_frame_vector_pooled if best_frame_vector_pooled is not None else np.array([])
                    }
                )

            # ============================================================
            # PROACTIVE MODE: Trigger proactive response using first frame
            # ============================================================
            # Use first frame embedding to query PMG for proactive care response
            if enable_proactive and len(buffer_embs) > 0:
                first_frame_emb_pooled = buffer_embs[0]
                first_frame = buffer_frames[0] if len(buffer_frames) > 0 else None

                print(f"\n{'='*60}")
                print(f"PROACTIVE MODE TRIGGERED")
                print(f"{'='*60}")
                print(f"Using first frame embedding from buffer (frame {selected_ids[idx - len(buffer_frames) + 1] if idx >= len(buffer_frames) else frame_idx})")
                
                proactive_response = proactive_user_query(
                    model=model,
                    processor=processor,
                    pmg=pmg,
                    first_frame_embedding=first_frame_emb_pooled,
                    short_term_memory_img_emb=short_term_memory_img_emb,
                    projection_mlp=projection_mlp,
                    current_frame=first_frame,
                    top_k=5
                )

                if proactive_response:
                    print(f"\nPROACTIVE ALERT GENERATED:")
                    print(f"   {proactive_response}")
                    print(f"{'='*60}\n")
                    qna_pairs.append(("[PROACTIVE]", proactive_response, "N/A"))
                else:
                    print(f"\nModel chose to stay silent (no proactive response needed)")
                    print(f"{'='*60}\n")

            buffer_frames = []
            buffer_embs = []
            frame_similarities = []

        for q in queries:
            if "answered" not in q:
                q["answered"] = False
            if q["frame"] <= frame_idx and q["answered"] == False:
                q["answered"] = True
                start_time = time.time()
                answer = passive_user_query(model, processor, pmg, q["query"], short_term_memory_img_emb)
                end_time = time.time()
                query_ans_time = end_time - start_time
                print(f"Answered query in {query_ans_time}s.")
                qna_pairs.append((q["query"], answer, f"{query_ans_time}s"))
        
        idx += 1
    
    for q in queries:
        if "answered" not in q or q["answered"] == False:
            start_time = time.time()
            # Use the last processed frame if available
            last_frame = all_frames[idx-1] if idx > 0 and idx <= len(all_frames) else None
            answer = passive_user_query(model, processor, pmg, q["query"], short_term_memory_img_emb)
            end_time = time.time()
            query_ans_time = end_time - start_time
            print(f"Answered query in {query_ans_time}s.")
            qna_pairs.append((q["query"], answer, f"{query_ans_time}s"))

    for e in qna_pairs:
        question, answer, ans_time = e
        print("Question:", question)
        print("Answer:", answer)
        print("Answer Time:", ans_time)
    print(pmg)
    print(pmg.get_stats())

# Usage
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-GPU Video Processing with Memory")
    parser.add_argument("--api_key", type=str, help="OpenAI API key for triplet extraction")
    parser.add_argument("--video_path", type=str, 
                       default="sample_videos/7246228-hd_1920_1080_24fps.mp4",
                       help="Path to the video file")
    parser.add_argument("--model_path", type=str,
                       default="Qwen/Qwen2.5-Omni-7B",
                       help="Path to the model")
    parser.add_argument("--projection_model_path", type=str,
                       default="ckpts/projection_mlp.pt",
                       help="Path to the projection model")
    parser.add_argument("--gamma_threshold", type=float, default=0.2, help="Similarity threshold for memory gate")
    parser.add_argument("--buffer_size", type=int, default=4, help="Buffer size for frame processing")
    parser.add_argument("--queue_size", type=int, default=1, help="Queue size for frame processing")
    parser.add_argument("--pool_len", type=int, default=4, help="Pool height & width for adaptive pooling (default: 8x8 patches, recommended: 8-16)")
    parser.add_argument("--enable_proactive", action="store_true", default=True, help="Enable proactive mode responses (default: True)")
    parser.add_argument("--disable_proactive", action="store_false", dest="enable_proactive", help="Disable proactive mode responses")

    args = parser.parse_args()
    
    # Video embedding
    print("Starting video processing...")
    print(f"Available GPUs: {torch.cuda.device_count()}")
    print(f"API Key provided: {'Yes' if args.api_key else 'No (will use environment variable)'}")
    print(f"Pooling configuration: {args.pool_len}x{args.pool_len} patches (default: 8x8, recommended: 8-16 for quality)")
    print(f"Proactive mode: {'ENABLED' if args.enable_proactive else 'DISABLED'}")

    queries = [
        {"query": "What is in the video?", "frame": 100},
        {"query": "Where am I?", "frame": 10},
    ]

    video_stream_with_memory(
        queries=queries,
        video_path=args.video_path,
        model_path=args.model_path,
        projection_model_path=args.projection_model_path,
        buffer_size=args.buffer_size,
        queue_size=args.queue_size,
        gamma_threshold=args.gamma_threshold,
        api_key=args.api_key,
        pool_size=(args.pool_len, args.pool_len),
        enable_proactive=args.enable_proactive
    )
    print("Multi-GPU processing completed successfully!")