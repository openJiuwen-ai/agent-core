import datetime
import math

import torch
import numpy as np
from PIL import Image

from src.utils.perstream_utils import cosine_similarity, get_text_embedding

def find_optimal_image_size(target_patches, patch_size=14):
    # Given the number of pooled patches (after 2x2 merge), calculate the image size
    # Each pooled patch comes from a 2x2 grid of raw patches
    # So: target_patches pooled patches = target_patches * 4 raw patches
    # For example: 16 pooled patches = 64 raw patches = 8x8 grid -> 112x112 image (8 * 14)
    raw_patches = target_patches * 4  # 2x2 merge means 4 raw patches per pooled patch
    grid_side = math.ceil(math.sqrt(raw_patches))
    return grid_side * patch_size

def proactive_user_query(model, processor, pmg, first_frame_embedding, short_term_memory_img_emb, projection_mlp, current_frame=None, top_k=5, verbose=False, silence_token_id=None):
    """
    Proactive mode response triggered by first frame in buffer.

    Args:
        model: Multi-GPU Qwen model
        processor: Qwen processor
        pmg: PersonalizedMemoryGraph instance
        first_frame_embedding: Embedding of first frame v_{t_s} in buffer
        short_term_memory_img_emb: FIFO queue of recent frame embeddings
        current_frame: Current frame for getting dimensions (optional)
        top_k: Number of top memories to retrieve
        delta_dfs_threshold: Minimum similarity for memory retrieval
        silence_token_id: Token ID for </silence> to force stop generation

    Returns:
        str: Generated proactive response A_{t_e}, or None if model outputs [EOS] (stays silent)
    """
    if verbose: print(f"Processing proactive mode with first frame embedding...")

    # Step 1: Retrieve relevant memories from PMG using first frame embedding
    # This implements the Retrieve() operation mentioned in the equation
    # Convert embedding to tensor if needed for similarity computation
    query_embedding_for_retrieval = projection_mlp(
        torch.from_numpy(first_frame_embedding).float().to(model.device).unsqueeze(0)
    ).cpu().detach().numpy().reshape(-1)

    retrieved_memories = pmg.retrieve_by_embedding(
        query_embedding=query_embedding_for_retrieval,
        top_k=top_k
    )

    if verbose:
        print(f"Retrieved {len(retrieved_memories)} relevant memories")
        print("Memories:")
        for idx, memory in enumerate(retrieved_memories):
            print(f"M{idx}:{memory}")
        print("----------------------------------------")


    # Step 2: Construct text input I_text = f_embed(M̃)
    # Combine retrieved memory context WITHOUT user question (key difference from passive mode)
    memory_context = ""

    # Create list to store visual vectors with their temporal ordering
    visual_vectors_with_time = []

    current_time = datetime.datetime.now()
    for vec in short_term_memory_img_emb:
        visual_vectors_with_time.append({
            'vector': vec,
            'created_at': current_time,
            'source': 'short_term'
        })

    if retrieved_memories:
        memory_texts = []
        for i, memory in enumerate(retrieved_memories):
            # Extract memory information
            memory_info = f"Memory {i+1}:\n"
            memory_info += f"- Category: {memory['category']}\n"
            memory_info += f"- Description: {memory['caption_text']}\n"

            # Get the timestamp for this memory
            memory_created_at = memory.get('created_at', current_time)

            # Add visual vectors with timestamps
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
            memory_texts.append(memory_info)
        memory_context = "\n".join(memory_texts)
        if verbose: print(f"Memory context length: {len(memory_context)} characters")
    else:
        memory_context = "No relevant memories found."
        if verbose: print("No relevant memories retrieved")

    # Sort visual vectors by temporal ordering (oldest first)
    visual_vectors_with_time.sort(key=lambda x: x['created_at'])

    # Extract just the vectors for processing
    visual_vectors = [item['vector'] for item in visual_vectors_with_time]

    if verbose:
        print(f"Visual vectors sorted by temporal order ({len(visual_vectors)} total):")
        for idx, item in enumerate(visual_vectors_with_time):
            print(f"  {idx}: {item['source']} - created at {item['created_at']}")

    # Step 3: Prepare the complete text input for proactive mode
    # This represents I_text in the equation
    # Key difference: NO user question, just memory context
    # complete_prompt = f"""Based on the following memories and recent visual context, decide whether to provide a proactive care response or stay silent.

    # RETRIEVED MEMORIES (in temporal order):
    # {memory_context}

    # Provide a helpful proactive response if there is something important to mention based on the memories and context. If there is nothing important to mention, output [SILENT] to stay quiet."""

    complete_prompt = f"""Based on the following memories and recent visual context, provide a proactive care response.

    RETRIEVED MEMORIES (in temporal order):
    {memory_context}

    Provide a helpful proactive response based on the memories and context.
    Output </silence> if there is no need to say anything.
    """

    if verbose: print(f"Generated proactive prompt length: {len(complete_prompt)} characters")

    # Step 4: Collect pooled visual embeddings AND text embeddings from memories
    # filter None values
    visual_vectors = [vec for vec in visual_vectors if vec is not None]
    pooled_visual_embeddings = [torch.from_numpy(vec).to(model.device) for vec in visual_vectors]

    if verbose:
        print("Shape of pooled visual embeddings:")
        for vec in pooled_visual_embeddings:
            print(f"  {vec.shape}")
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
        # Monkey-patch
        original_get_image_features = model.get_image_features

        def custom_get_image_features(pixel_values, image_grid_thw):
            return combined_pooled

        model.get_image_features = custom_get_image_features

        inputs['pixel_values'] = torch.zeros_like(inputs['pixel_values'])

        # Setup EOS tokens - include </silence> to force stop
        eos_token_ids = [processor.tokenizer.eos_token_id]
        if silence_token_id is not None:
            eos_token_ids.append(silence_token_id)

        outputs = model.generate(
            **inputs,
            max_new_tokens=200,
            do_sample=False,
            return_dict_in_generate=True,
            pad_token_id=processor.tokenizer.eos_token_id,
            eos_token_id=eos_token_ids  # Stop at EOS or </silence>
        )

        model.get_image_features = original_get_image_features

        generated_tokens = outputs.sequences[0][len(inputs['input_ids'][0]):]
        # Decode without skipping special tokens to check for </silence>
        response_with_tokens = processor.decode(generated_tokens, skip_special_tokens=False)
        response = processor.decode(generated_tokens, skip_special_tokens=True)

    if verbose:
        print(f"Generated proactive response (with tokens): {response_with_tokens}")
        print(f"Generated proactive response length: {len(response)} characters")

    # Check if model chose to stay silent
    if "</silence>" in response_with_tokens or "</silence>" in response or response.strip() == "":
        print("Model chose to stay silent (no proactive response needed)")
        return "</silence>"
    else:
        return response.strip()