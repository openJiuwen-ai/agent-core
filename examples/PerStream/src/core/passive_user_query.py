import torch
import numpy as np
from PIL import Image
from src.utils.perstream_utils import cosine_similarity, get_text_embedding
import datetime
import time

def find_optimal_image_size(target_patches, patch_size=14):
    # Given the number of pooled patches (after 2x2 merge), calculate the image size
    # Each pooled patch comes from a 2x2 grid of raw patches
    # So: target_patches pooled patches = target_patches * 4 raw patches
    # For example: 16 pooled patches = 64 raw patches = 8x8 grid -> 112x112 image (8 * 14)
    import math
    raw_patches = target_patches * 4  # 2x2 merge means 4 raw patches per pooled patch
    grid_side = math.ceil(math.sqrt(raw_patches))
    return grid_side * patch_size

def passive_user_query(model, processor, pmg, query, short_term_memory_img_emb, top_k=5, delta_dfs_threshold=0.3, verbose=False, output_retrieval_time=False):
    """
    Passive mode response triggered by user query.

    Uses matched token count approach - creates dummy images that produce exactly
    the right number of patches to match pooled embeddings (no upsampling needed).
    """
    if verbose: print(f"Processing user query: {query}")

    # Step 1: Retrieve relevant memories from PMG
    t0 = time.time()
    retrieved_memories = pmg.retrieve(
        query=query,
        top_k=top_k,
        delta_dfs_threshold=delta_dfs_threshold
    )
    t1 = time.time()
    retrieval_time = t1 - t0

    if verbose: print(f"Retrieved {len(retrieved_memories)} relevant memories")

    # Step 2: Construct text input
    memory_context = ""
    visual_vectors_with_time = []

    current_time = datetime.datetime.now()
    for vec in short_term_memory_img_emb:
        visual_vectors_with_time.append({
            'vector': vec,
            'created_at': current_time,
            'source': 'short_term'
        })

    # Match training format: simple numbered list
    if retrieved_memories:
        memory_context = "Memories:\n"
        for i, memory in enumerate(retrieved_memories, 1):
            # Simple format matching training data
            memory_context += f"{i}. {memory['caption_text']}\n"

            memory_created_at = memory.get('created_at', current_time)

            if len(memory['mean_visual_vector']) > 0:
                visual_vectors_with_time.append({
                    'vector': memory['mean_visual_vector'],
                    'created_at': memory_created_at,
                    'source': f"memory_{i}_mean"
                })
            if len(memory['nearest_visual_vector']) > 0:
                visual_vectors_with_time.append({
                    'vector': memory['nearest_visual_vector'],
                    'created_at': memory_created_at,
                    'source': f"memory_{i}_nearest"
                })

        # Truncate if too long (matching training logic)
        if len(memory_context) > 1000:
            memory_context = memory_context[:1000] + "..."
    else:
        memory_context = ""

    # Step 3: Prepare prompt - match training format exactly
    # Training uses: memory_context + "\n" + query (with video frames embedded)
    # So we just combine memory_context with the query directly
    if memory_context:
        complete_prompt = f"{memory_context}\n{query}"
    else:
        complete_prompt = query

    visual_vectors_with_time.sort(key=lambda x: x['created_at'])
    if verbose:
        for item in visual_vectors_with_time:
            print(f"{item['source']}: {item['vector'].shape}")

    visual_vectors = [item['vector'] for item in visual_vectors_with_time]

    # filter None values
    visual_vectors = [vec for vec in visual_vectors if vec is not None]
    pooled_visual_embeddings = [
        torch.from_numpy(vec).to(model.device) if isinstance(vec, np.ndarray) 
        else vec.to(model.device) if isinstance(vec, torch.Tensor)
        else vec
        for vec in visual_vectors
    ]

    if verbose:
        print("Shape of pooled visual embeddings:")
        for vec in pooled_visual_embeddings:
            print(f"{vec.shape}")
        print(f"Collected {len(pooled_visual_embeddings)} pooled visual embeddings (NO UPSAMPLING)")

    # Step 5: Generate response WITHOUT upsampling
    # Determine patch count from LARGEST embedding (visual embeddings are multi-patch)
    hidden_dim = model.config.vision_config.out_hidden_size
    num_pooled_patches = 1  # Initialize with minimum value

    for emb in pooled_visual_embeddings:
        emb_check = emb.unsqueeze(0) if emb.ndim == 1 else emb
        total_size = emb_check.numel()
        num_patches = total_size // hidden_dim
        num_pooled_patches = max(num_pooled_patches, num_patches)

    if verbose: print(f"  Pool configuration: {num_pooled_patches} patches (max across all embeddings)")

    # Stack pooled embeddings first to get total patch count
    stacked_pooled = []
    for pooled_emb in pooled_visual_embeddings:
        if pooled_emb.ndim == 1:
            pooled_emb = pooled_emb.unsqueeze(0)

        # Multi-patch - reshape to [num_patches, hidden_dim]
        pooled_emb = pooled_emb.reshape(-1, hidden_dim)

        stacked_pooled.append(pooled_emb)

    # Concatenate: [N * num_pooled_patches, hidden_dim]
    combined_pooled = torch.cat(stacked_pooled, dim=0)
    total_patches = combined_pooled.shape[0]
    if verbose: print(f"\nCombined pooled embeddings (visual + text): {combined_pooled.shape}")

    # Use dummy video where number of frames = number of embeddings
    # Each frame produces num_pooled_patches patches
    num_frames = len(stacked_pooled)

    # Create dummy frames (one per embedding)
    # Each frame should produce num_pooled_patches patches
    frame_img_size = find_optimal_image_size(num_pooled_patches)
    dummy_frames = [Image.new('RGB', (frame_img_size, frame_img_size), color='white') for _ in range(num_frames)]

    if verbose:
        print(f"  Using {num_frames} dummy frames of {frame_img_size}×{frame_img_size} (each produces {num_pooled_patches} patches)")
        print(f"  Total patches: {num_frames} frames × {num_pooled_patches} patches = {num_frames * num_pooled_patches} patches")

    # Prepare conversation with multiple frames (video)
    conversation = [{
        "role": "user",
        "content": [
            {"type": "text", "text": complete_prompt}
        ] + [{"type": "image", "image": frame} for frame in dummy_frames]
    }]

    text_input = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
    if isinstance(text_input, list):
        text_input = text_input[0]

    inputs = processor(
        text=[text_input],
        images=dummy_frames,
        return_tensors="pt"
    ).to(model.device)

    with torch.no_grad():
        if verbose:
            print(f"  Direct pooled shape: {combined_pooled.shape} (NO UPSAMPLING)")

        # Monkey-patch
        original_get_image_features = model.get_image_features

        def custom_get_image_features(pixel_values, image_grid_thw):
            return combined_pooled

        model.get_image_features = custom_get_image_features

        inputs['pixel_values'] = torch.zeros_like(inputs['pixel_values'])

        outputs = model.generate(
            **inputs,
            max_new_tokens=200,
            do_sample=True,
            temperature=1.0,
            return_dict_in_generate=True,
            pad_token_id=processor.tokenizer.eos_token_id,
            eos_token_id=processor.tokenizer.eos_token_id
        )

        model.get_image_features = original_get_image_features

        generated_tokens = outputs.sequences[0][len(inputs['input_ids'][0]):]
        response = processor.decode(generated_tokens, skip_special_tokens=True)

    if output_retrieval_time:
        return response.strip(), retrieval_time
    else:
        return response.strip()