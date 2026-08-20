import numpy as np
import torch
import os
import json
import datetime
import re
import time
from openai import OpenAI
from sentence_transformers import SentenceTransformer

def cosine_similarity(vec1, vec2):
    """Debug version of cosine similarity"""
    vec1_flat = vec1.flatten().astype(np.float32)
    vec2_flat = vec2.flatten().astype(np.float32)
    
    dot_product = np.dot(vec1_flat, vec2_flat)
    norm1 = np.linalg.norm(vec1_flat)
    norm2 = np.linalg.norm(vec2_flat)
    
    return dot_product / (norm1 * norm2) if norm1 != 0 and norm2 != 0 else 0.0

model = SentenceTransformer('all-MiniLM-L6-v2')
model = model.to('cuda')
def get_text_embedding(text, model=model):
    """Extract embedding from text using Sentence Transformers
    
    Args:
        text: Input text string
        model: SentenceTransformer model (if None, loads default model)
    
    Returns:
        numpy array of embeddings
    """
    embedding = model.encode(text)
    return np.array(embedding).reshape(1, -1)

def get_triplet(
    caption,
    api_key=None,
    model="gpt-3.5-turbo",
    max_retries=3,
    retry_delay=1,
    verbose=False,
    max_triplets=2
):
    """
    Extract subject-predicate-object triplets using the newer OpenAI client with retry logic.
    
    Args:
        caption (str): Text to extract triplet from
        api_key (str, optional): OpenAI API key (required if not set in environment)
        model (str): OpenAI model to use
        max_retries (int): Maximum number of retry attempts (default: 3)
        retry_delay (int): Delay between retries in seconds (default: 1)
        verbose (bool): Whether to print debug information (default: False)
        max_triplets (int): Maximum number of triplets to extract (default: 2)
    
    Returns:
        list: List of tuples [(subject, predicate, object), ...] or empty list if all attempts fail
    """

    if verbose:
        print(f"Processing caption: {caption}")

    # Initialize client
    if api_key:
        client = OpenAI(api_key=api_key)
    else:
        return ValueError("API key is required")
    
    prompt = f"""
        Extract up to {max_triplets} subject-predicate-object triplets from the following caption.
        Return a JSON array of objects, where each object has exactly three fields: "subject", "predicate", "object".
        Extract the most important relationships, prioritizing the main action and any secondary actions or relationships.
        
        The subject and object must only describe the noun itself. All other information (location, manner, instrument, etc.) should be in the predicate:
        - C picks a bottle from the shelf with his right hand. -> (C, picks from the shelf with right hand, a bottle)
        - C puts the bottle on the shelf -> (C, puts on the shelf, the bottle)
        - C opens the door and walks into the room -> [(C, opens, the door), (C, walks into, the room)]
        - Person holding in left hand a white cap with a logo and in right hand a paintbrush -> [(Person, holding in left hand, a white cap with a logo), (Person, holding in right hand, a paintbrush)]

        Caption: "{caption}"

        Response format (return up to {max_triplets} triplets):
        [
            {{"subject": "...", "predicate": "...", "object": "..."}},
            {{"subject": "...", "predicate": "...", "object": "..."}}
        ]
    """
    
    for attempt in range(max_retries):
        try:
            if verbose:
                print(f"Attempt {attempt + 1}/{max_retries}")
            
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an expert at extracting semantic triplets from text. Always respond with valid JSON array only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=100
            )
            
            response_text = response.choices[0].message.content.strip()
            response_text = re.sub(r'```json\s*', '', response_text)
            response_text = re.sub(r'```\s*$', '', response_text)
            
            triplet_list = json.loads(response_text)
            
            if not isinstance(triplet_list, list):
                if verbose:
                    print(f"Warning: Response is not a list on attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    return []
                time.sleep(retry_delay)
                continue
            
            result = []
            for triplet_data in triplet_list[:max_triplets]:
                subject = str(triplet_data.get("subject", "")).strip()
                predicate = str(triplet_data.get("predicate", "")).strip()
                obj = str(triplet_data.get("object", "")).strip()
                
                if subject and predicate and obj:
                    result.append((subject, predicate, obj))
                    if verbose:
                        print(f"Extracted triplet: ({subject}, {predicate}, {obj})")
                elif verbose:
                    print(f"Warning: Skipping incomplete triplet: {triplet_data}")
            
            if result:
                if verbose:
                    print(f"Successfully extracted {len(result)} triplet(s)")
                return result
            elif attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            else:
                return []
                    
        except json.JSONDecodeError as e:
            if verbose:
                print(f"Attempt {attempt + 1}: JSON parsing error: {e}")
            if attempt == max_retries - 1:
                return []
            time.sleep(retry_delay)
            continue
                
        except Exception as e:
            if verbose:
                print(f"Attempt {attempt + 1}: Error calling OpenAI API: {e}")
            if attempt == max_retries - 1:
                return []
            time.sleep(retry_delay)
            continue
    
    return []

def get_triplet_native(
    memory,
    model,
    processor,
    max_retries=3,
    retry_delay=1,
    verbose=False,
    max_triplets=2
):
    """
    Extract subject-predicate-object triplets using the model directly (no API calls).
    
    Args:
        memory (str): Text to extract triplet from
        model: The vision-language model to use for generation
        processor: The processor for the model
        max_retries (int): Maximum number of retry attempts (default: 3)
        retry_delay (int): Delay between retries in seconds (default: 1)
        verbose (bool): Whether to print debug information (default: False)
        max_triplets (int): Maximum number of triplets to extract (default: 2)
    
    Returns:
        list: List of tuples [(subject, predicate, object), ...] or empty list if all attempts fail
    """
    if verbose:
        print(f"Processing memory: {memory}")
    
    prompt = f"""Extract up to {max_triplets} subject-predicate-object triplets from the following caption.
Return a JSON array of objects, where each object has exactly three fields: "subject", "predicate", "object".
Extract the most important relationships, prioritizing the main action and any secondary actions or relationships.

The subject and object must only describe the noun itself. All other information (location, manner, instrument, etc.) should be in the predicate:
- C picks a bottle from the shelf with his right hand. -> (C, picks from the shelf with right hand, a bottle)
- C puts the bottle on the shelf -> (C, puts on the shelf, the bottle)
- C opens the door and walks into the room -> [(C, opens, the door), (C, walks into, the room)]
- Person holding in left hand a white cap with a logo and in right hand a paintbrush -> [(Person, holding in left hand, a white cap with a logo), (Person, holding in right hand, a paintbrush)]

Caption: "{memory}"

Response format (return up to {max_triplets} triplets):
[
    {{"subject": "...", "predicate": "...", "object": "..."}},
    {{"subject": "...", "predicate": "...", "object": "..."}}
]"""
    
    conversation = [{
        "role": "system",
        "content": "You are an expert at extracting semantic triplets from text. Always respond with valid JSON array only."
    }, {
        "role": "user",
        "content": prompt
    }]
    
    for attempt in range(max_retries):
        try:
            if verbose:
                print(f"Attempt {attempt + 1}/{max_retries}")
            
            text_input = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
            inputs = processor(text=text_input, return_tensors="pt").to(model.device)
            
            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=200,
                    do_sample=False,
                    temperature=0.1,
                    pad_token_id=processor.tokenizer.eos_token_id,
                    eos_token_id=processor.tokenizer.eos_token_id
                )
                response_text = processor.decode(outputs[0], skip_special_tokens=True)
            
            # Extract the assistant's response
            if "assistant" in response_text:
                response_text = response_text.split("assistant")[-1].strip()
            else:
                # If no "assistant" marker, try to find JSON array
                response_text = response_text.strip()
            
            # Clean up markdown code blocks if present
            response_text = re.sub(r'```json\s*', '', response_text)
            response_text = re.sub(r'```\s*$', '', response_text)
            response_text = response_text.strip()
            
            # Try to extract JSON array from response
            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(0)
            
            triplet_list = json.loads(response_text)
            
            if not isinstance(triplet_list, list):
                if verbose:
                    print(f"Warning: Response is not a list on attempt {attempt + 1}")
                if attempt == max_retries - 1:
                    return []
                time.sleep(retry_delay)
                continue
            
            result = []
            for triplet_data in triplet_list[:max_triplets]:
                subject = str(triplet_data.get("subject", "")).strip()
                predicate = str(triplet_data.get("predicate", "")).strip()
                obj = str(triplet_data.get("object", "")).strip()
                
                if subject and predicate and obj:
                    result.append((subject, predicate, obj))
                    if verbose:
                        print(f"Extracted triplet: ({subject}, {predicate}, {obj})")
                elif verbose:
                    print(f"Warning: Skipping incomplete triplet: {triplet_data}")
            
            if result:
                if verbose:
                    print(f"Successfully extracted {len(result)} triplet(s)")
                return result
            elif attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            else:
                return []
                    
        except json.JSONDecodeError as e:
            if verbose:
                print(f"Attempt {attempt + 1}: JSON parsing error: {e}")
                print(f"Response text: {response_text[:200] if 'response_text' in locals() else 'N/A'}")
            if attempt == max_retries - 1:
                return []
            time.sleep(retry_delay)
            continue
                
        except Exception as e:
            if verbose:
                print(f"Attempt {attempt + 1}: Error generating triplets: {e}")
            if attempt == max_retries - 1:
                return []
            time.sleep(retry_delay)
            continue
    
    return []

def get_image_embedding(image, model, processor, pool_size=(4, 4), for_storage=False):
    """
    Extract embedding from single image.

    Args:
        image: PIL Image to embed
        model: The vision-language model
        processor: The processor for the model
        pool_size: Tuple (H, W) for adaptive pooling size. Default (1, 1) for global pooling.
        for_storage: If True, return both EOS embedding (for comparison) and pooled features (for storage)

    Returns:
        If for_storage=True: tuple of (eos_embedding, pooled_embedding)
        Otherwise: embedding numpy array of shape [1, hidden_dim]
    """

    # Return both embeddings: EOS for comparison, pooled for storage
    # First get EOS embedding
    conversation = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Summarize all key information and objects visible in this image in 1 sentence max and indicate what is important to remember:"},
            {"type": "image", "image": image}
        ]
    }]

    text_input = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
    if isinstance(text_input, list):
        text_input = text_input[0]
    inputs = processor(text=[text_input], images=[image], return_tensors="pt").to(model.device)

    with torch.no_grad():
        # Now get pooled visual features from vision encoder (not raw pixels!)
        image_features = model.get_image_features(
            inputs.pixel_values,
            inputs.image_grid_thw
        )

        # Add batch dimension if needed
        if image_features.ndim == 2:
            image_features = image_features.unsqueeze(0)

        num_patches = image_features.shape[1]
        hidden_dim = image_features.shape[2]

        # Get spatial dimensions
        grid_thw = inputs.image_grid_thw[0]
        temporal = grid_thw[0].item()
        grid_h = grid_thw[1].item()
        grid_w = grid_thw[2].item()

        # Calculate actual feature grid dimensions (accounting for merge factor)
        merge_factor = (temporal * grid_h * grid_w) / num_patches
        actual_h = int(grid_h / (merge_factor ** 0.5))
        actual_w = int(grid_w / (merge_factor ** 0.5))

        # Reshape to [batch, height, width, channels]
        features_reshaped = image_features.reshape(1, actual_h, actual_w, hidden_dim)
        features_reshaped = features_reshaped.permute(0, 3, 1, 2)  # [batch, channels, H, W]

        pooled_features = torch.nn.functional.adaptive_avg_pool2d(
            features_reshaped,
            pool_size
        )

        pool_h, pool_w = pool_size
        pooled_features = pooled_features.permute(0, 2, 3, 1).reshape(1, pool_h * pool_w, -1)
        # Convert from bfloat16 to float32 before numpy conversion (numpy doesn't support bfloat16)
        if pooled_features.dtype == torch.bfloat16:
            pooled_features = pooled_features.float()
        pooled_embedding = pooled_features.reshape(pool_h * pool_w, -1).cpu().numpy()

    return pooled_embedding


def remember_gate(frame, memory_subclass_embedding_matrix, category_names, model, processor, projection_mlp, gamma_threshold=0.5, pool_size=(8, 8)):
    """Remember gate using model - returns pooled embeddings"""
    # Get pooled embeddings for storage
    frame_embedding_pooled = get_image_embedding(frame, model, processor, for_storage=True, pool_size=pool_size)
    projected = projection_mlp(
        torch.from_numpy(frame_embedding_pooled).float().to(model.device).unsqueeze(0)
    ).cpu().detach()
    # Convert from bfloat16 to float32 before numpy conversion
    if projected.dtype == torch.bfloat16:
        projected = projected.float()
    frame_embedding_pooled_projected = projected.numpy()

    # Compute cosine similarities between frame_embedding_pooled and memory_subclass_embedding_matrix
    # frame_embedding_pooled: [num_patches, hidden_dim] e.g., [64, 3584] for 8x8 pooling
    # memory_subclass_embedding_matrix: [num_categories, hidden_dim] e.g., [9, 3584]
    # Result: [num_patches, num_categories] e.g., [64, 9]
    cosine_similarities = np.dot(frame_embedding_pooled_projected, memory_subclass_embedding_matrix.T) / (
        np.linalg.norm(frame_embedding_pooled_projected, axis=1, keepdims=True) * 
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
    
    # Remember if similarity exceeds threshold
    should_remember = max_similarity > gamma_threshold

    return should_remember, max_similarity, best_match_category, frame_embedding_pooled

def generate_buffer_caption(buffer_frames, model, processor):
    """Generate caption from buffer frames"""
    conversation = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe and summarize all key information and objects visible in these frames and indicate what is important to remember in 200 chars:"}
        ] + [{"type": "image", "image": frame} for frame in buffer_frames]
    }]
    
    text_input = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=text_input, images=buffer_frames, return_tensors="pt").to(model.device)
    
    with torch.no_grad():
        # Generate caption with output_hidden_states=True to get embeddings
        outputs = model.generate(
            **inputs, 
            max_new_tokens=200, 
            do_sample=False,
            pad_token_id=processor.tokenizer.eos_token_id,
            eos_token_id=processor.tokenizer.eos_token_id
        )
        caption = processor.decode(outputs[0], skip_special_tokens=True)

    caption = caption.split("assistant\n")[-1]
    return caption