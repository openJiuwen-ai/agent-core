"""
LoRA Fine-tuning script for Qwen2.5-Omni on Memory-based Video QA Dataset
Handles video frames + question-answer pairs with proper masking (only answer contributes to loss)
"""

import os
import json
import math
import torch
import argparse
import numpy as np
import random
from tqdm import tqdm
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from transformers import (
    Qwen2_5OmniProcessor,
    Qwen2_5OmniThinkerForConditionalGeneration,
    Trainer,
    TrainingArguments,
    DataCollatorForLanguageModeling,
)
from peft import (
    LoraConfig,
    get_peft_model,
    prepare_model_for_kbit_training,
    TaskType
)
from torch.utils.data import Dataset
import transformers

from memory_dataset_class import MemoryDataset


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(description="LoRA Fine-tuning for Qwen2.5-Omni")
    
    # Model arguments
    parser.add_argument("--model-path", type=str, required=True,
                       help="Path to pretrained Qwen2.5-Omni model")
    parser.add_argument("--output-dir", type=str, required=True,
                       help="Directory to save fine-tuned model")
    
    # Data arguments
    parser.add_argument("--video-dir", type=str, required=True,
                       help="Directory containing video files")
    parser.add_argument("--data-file", type=str, required=True,
                       help="Path to data JSON file (contains train/val splits)")
    parser.add_argument("--val-split-name", type=str, default="val",
                       help="Name of validation split in data (default: 'val')")
    parser.add_argument("--train-split-name", type=str, default="train",
                       help="Name of training split in data (default: 'train')")
    
    # LoRA arguments
    parser.add_argument("--lora-r", type=int, default=8,
                       help="LoRA attention dimension")
    parser.add_argument("--lora-alpha", type=int, default=16,
                       help="LoRA alpha parameter")
    parser.add_argument("--lora-dropout", type=float, default=0.05,
                       help="LoRA dropout probability")
    parser.add_argument("--lora-target-modules", nargs="+",
                       default=["q_proj", "k_proj", "v_proj", "o_proj"],
                       help="Target modules for LoRA")
    
    # Training arguments
    parser.add_argument("--num-train-epochs", type=int, default=3,
                       help="Number of training epochs")
    parser.add_argument("--per-device-train-batch-size", type=int, default=1,
                       help="Training batch size per device")
    parser.add_argument("--per-device-eval-batch-size", type=int, default=1,
                       help="Evaluation batch size per device")
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8,
                       help="Gradient accumulation steps")
    parser.add_argument("--learning-rate", type=float, default=2e-4,
                       help="Learning rate")
    parser.add_argument("--warmup-ratio", type=float, default=0.03,
                       help="Warmup ratio")
    parser.add_argument("--weight-decay", type=float, default=0.01,
                       help="Weight decay")
    parser.add_argument("--max-grad-norm", type=float, default=1.0,
                       help="Max gradient norm for clipping")
    parser.add_argument("--lr-scheduler-type", type=str, default="cosine",
                       help="Learning rate scheduler type")
    
    # Optimization arguments
    parser.add_argument("--bf16", action="store_true",
                       help="Use bfloat16 mixed precision")
    parser.add_argument("--fp16", action="store_true",
                       help="Use float16 mixed precision")
    parser.add_argument("--gradient-checkpointing", action="store_true",
                       help="Enable gradient checkpointing")
    parser.add_argument("--optim", type=str, default="adamw_torch",
                       help="Optimizer (adamw_torch, adamw_8bit, etc.)")
    
    # Logging and saving
    parser.add_argument("--logging-steps", type=int, default=10,
                       help="Log every N steps")
    parser.add_argument("--save-steps", type=int, default=200,
                       help="Save checkpoint every N steps")
    parser.add_argument("--eval-steps", type=int, default=100,
                       help="Evaluate every N steps")
    parser.add_argument("--save-total-limit", type=int, default=3,
                       help="Maximum number of checkpoints to keep")
    
    # Video processing arguments
    parser.add_argument("--max-frames", type=int, default=8,
                       help="Maximum frames to extract per video")
    parser.add_argument("--image-size", type=int, default=224,
                       help="Image size for video frames")
    parser.add_argument("--target-fps", type=float, default=1.0,
                       help="Target FPS for frame sampling")
    parser.add_argument("--video-start-before", type=float, default=1.0,
                       help="Seconds to extract before timestamp")
    parser.add_argument("--video-end-after", type=float, default=0.5,
                       help="Seconds to extract after timestamp")
    parser.add_argument("--video-extensions", nargs="+",
                       default=[".mp4", ".avi", ".mov", ".mkv"],
                       help="Video file extensions")
    
    # Memory-specific arguments
    parser.add_argument("--memory-types", nargs="+",
                       default=["type_1_memories", "type_2_memories"],
                       help="Types of memories to include")
    parser.add_argument("--memory-format", type=str, default="structured",
                       choices=["structured", "narrative"],
                       help="How to format memory context")
    parser.add_argument("--memory-strategy", type=str, default="none",
                       help="Memory retrieval strategy")
    parser.add_argument("--memory-cache", type=str, default=None,
                       help="Path to cached PMG-retrieved memories JSON file")
    
    # System arguments
    parser.add_argument("--seed", type=int, default=42,
                       help="Random seed")
    parser.add_argument("--num-workers", type=int, default=4,
                       help="Number of data loading workers")
    parser.add_argument("--low-memory", action="store_true",
                       help="Enable low memory mode")

    parser.add_argument("--deepspeed", type=str, default=None,
                       help="Path to DeepSpeed config file")
    parser.add_argument("--local_rank", type=int, default=-1,
                       help="Local rank for distributed training (set by DeepSpeed)")
    
    return parser.parse_args()


class VideoMemoryQADataset(Dataset):
    """
    Dataset that combines MemoryDataset with proper formatting for training
    Outputs: question + answer with answer-only masking
    """

    def __init__(self, questions, video_dir, processor, model_config, args, memory_cache=None):
        # Use MemoryDataset as base
        self.base_dataset = MemoryDataset(
            questions=questions,
            video_dir=video_dir,
            processor=processor,
            model_config=model_config,
            args=args
        )
        self.processor = processor
        self.args = args

        # Load memory cache if provided
        self.memory_cache = {}
        if memory_cache is not None:
            if isinstance(memory_cache, str):
                # Load from file
                print(f"Loading memory cache from {memory_cache}...")
                with open(memory_cache) as f:
                    cache_data = json.load(f)
                    self.memory_cache = {item['sample_id']: item for item in cache_data}
                print(f"Loaded {len(self.memory_cache)} cached memory entries")
            elif isinstance(memory_cache, dict):
                self.memory_cache = memory_cache
    
    def __len__(self):
        return len(self.base_dataset)
    
    def __getitem__(self, idx):
        """
        Returns properly formatted training sample with:
        - Video frames (as list of PIL Images)
        - Question + Answer text with proper image placeholders
        - Raw components for label masking
        """
        # Get base data from MemoryDataset
        data = self.base_dataset[idx]

        video_tensor = data['video_tensor']
        question = data['question']
        answer = data['answer']
        sample_id = data['sample_id']

        # Use cached retrieved memories if available, otherwise use all memories
        if sample_id in self.memory_cache:
            memories = self.memory_cache[sample_id].get('retrieved_memories', [])
            # Fallback to all memories if no retrieved memories
            if not memories:
                memories = data['memories']
        else:
            memories = data['memories']

        # Convert video tensor to list of PIL Images
        # video_tensor shape: (num_frames, C, H, W) with values in [0, 255]
        from PIL import Image
        video_frames = []
        for frame_idx in range(video_tensor.shape[0]):
            # Get single frame: (C, H, W)
            frame = video_tensor[frame_idx]
            # Convert to (H, W, C) and ensure uint8
            frame_np = frame.permute(1, 2, 0).cpu().numpy().astype(np.uint8)
            pil_img = Image.fromarray(frame_np)
            video_frames.append(pil_img)

        # For Qwen multimodal, we need to create a conversation with image tags
        # Format: <|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>Question<|im_end|>
        # The processor will replace <|image_pad|> with actual image embeddings

        # Create image placeholders for each frame
        image_tokens = "<|vision_start|><|image_pad|><|vision_end|>"
        # Add one image token per frame
        video_placeholder = "".join([image_tokens] * len(video_frames))

        user_message = f"{video_placeholder}{question}"

        # Ensure answer ends with </response> or </silence> tag
        # This matches the format used in eval_passive_dataset.py
        if not answer.strip().endswith("</response>") and not answer.strip().endswith("</silence>"):
            # If answer is empty or just whitespace, it's a silence case
            if not answer.strip():
                assistant_message = "</silence>"
            else:
                # Otherwise, ensure it ends with </response>
                assistant_message = f"{answer.strip()}</response>"
        else:
            assistant_message = answer.strip()

        memory_context = "Memories:\n"
        for i, memory in enumerate(memories, 1):
            memory_context += f"{i}. {memory}\n"

        if len(memory_context) > 1000:
            memory_context = memory_context[:1000] + "..."
        # Build full conversation
        full_text = f"<|im_start|>user\n{memory_context}\n{user_message}<|im_end|>\n<|im_start|>assistant\n{assistant_message}<|im_end|>"

        return {
            'video_frames': video_frames,  # List of PIL Images
            'text': full_text,
            'question_text': user_message,
            'answer_text': assistant_message,
            'memory_context': memory_context,
            'sample_id': data['sample_id']
        }


@dataclass
class VideoMemoryDataCollator:
    """
    Custom data collator that:
    1. Processes video frames + text with proper vision tokens
    2. Creates labels with masking (only answer tokens contribute to loss)
    """
    
    processor: Qwen2_5OmniProcessor
    padding: Union[bool, str] = True
    max_length: Optional[int] = None
    pad_to_multiple_of: Optional[int] = None
    return_tensors: str = "pt"
    
    def __call__(self, features: List[Dict]) -> Dict[str, torch.Tensor]:
        """
        Collate batch of samples with proper masking
        Process text and images together using processor to ensure proper alignment
        """
        batch_size = len(features)

        # Extract components
        video_frames_list = [f['video_frames'] for f in features]  # List of lists of PIL Images
        texts = [f['text'] for f in features]
        question_texts = [f['question_text'] for f in features]
        answer_texts = [f['answer_text'] for f in features]
        memory_contexts = [f.get('memory_context', '') for f in features]
        
        # Process each sample individually with processor to ensure image-text alignment
        # CRITICAL: Using processor.__call__() processes text + images together, which ensures
        # the number of image tokens in the tokenized text exactly matches the number of images.
        # This prevents "image feature and image tokens do not match" errors that occur when
        # processing text and images separately or when device/version differences cause tokenization variations.
        processed_samples = []
        all_pixel_values = []
        all_image_grid_thw = []
        
        for i, (text, frames) in enumerate(zip(texts, video_frames_list)):
            if frames and len(frames) > 0:
                # Use processor to process text + images together
                # The text already contains <|vision_start|><|image_pad|><|vision_end|> tokens
                # The processor will align these tokens with the provided images
                inputs = self.processor(
                    text=text,
                    images=frames,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=self.max_length if self.max_length else 2048,
                )
                
                # Extract processed components
                sample_input_ids = inputs['input_ids'].squeeze(0)  # Remove batch dim
                sample_attention_mask = inputs.get('attention_mask', torch.ones_like(sample_input_ids)).squeeze(0)
                
                # Get pixel_values and image_grid_thw if present
                if 'pixel_values' in inputs:
                    pixel_vals = inputs['pixel_values']  # Shape: (num_images, C, H, W)
                    all_pixel_values.append(pixel_vals)
                    
                    # Check if processor returned image_grid_thw automatically
                    if 'image_grid_thw' in inputs:
                        grid_thw = inputs['image_grid_thw']
                        if grid_thw.ndim == 2:
                            all_image_grid_thw.append(grid_thw)
                        else:
                            # If shape is wrong, create it manually
                            num_images = pixel_vals.shape[0] if pixel_vals.ndim == 4 else len(frames)
                            h = pixel_vals.shape[2] if pixel_vals.ndim == 4 else 224
                            w = pixel_vals.shape[3] if pixel_vals.ndim == 4 else 224
                            patch_size = 14
                            h_patches = max(1, h // patch_size)
                            w_patches = max(1, w // patch_size)
                            grid_thw = torch.tensor(
                                [[1, h_patches, w_patches] for _ in range(num_images)],
                                dtype=torch.long
                            )
                            all_image_grid_thw.append(grid_thw)
                    else:
                        # Create image_grid_thw manually
                        if pixel_vals.ndim == 4:
                            num_images = pixel_vals.shape[0]
                            h = pixel_vals.shape[2]
                            w = pixel_vals.shape[3]
                        else:
                            num_images = len(frames)
                            h = w = 224  # Default
                        
                        # Calculate patch grid dimensions (assuming 14x14 patch size)
                        patch_size = 14
                        h_patches = max(1, h // patch_size)
                        w_patches = max(1, w // patch_size)
                        
                        # Create grid_thw: one row per image [t=1, h_patches, w_patches]
                        grid_thw = torch.tensor(
                            [[1, h_patches, w_patches] for _ in range(num_images)],
                            dtype=torch.long
                        )
                        all_image_grid_thw.append(grid_thw)
                    
                    # Validation: Count image tokens in input_ids and verify they match number of images
                    # Count occurrences of image token IDs (if available)
                    # This helps catch mismatches early
                    num_expected_images = len(frames)
                    if pixel_vals.ndim == 4:
                        num_actual_images = pixel_vals.shape[0]
                    else:
                        num_actual_images = num_expected_images
                    
                    if num_actual_images != num_expected_images:
                        print(f"Warning: Sample {i}: Expected {num_expected_images} images, got {num_actual_images} pixel_value tensors")
                
                processed_samples.append({
                    'input_ids': sample_input_ids,
                    'attention_mask': sample_attention_mask,
                })
            else:
                # Text-only sample
                inputs = self.processor.tokenizer(
                    text,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=self.max_length if self.max_length else 2048,
                )
                processed_samples.append({
                    'input_ids': inputs['input_ids'].squeeze(0),
                    'attention_mask': inputs.get('attention_mask', torch.ones_like(inputs['input_ids'])).squeeze(0),
                })
        
        # Pad sequences to same length
        max_length = max(s['input_ids'].shape[0] for s in processed_samples)
        
        input_ids = torch.zeros((batch_size, max_length), dtype=torch.long)
        attention_mask = torch.zeros((batch_size, max_length), dtype=torch.long)
        
        for i, sample in enumerate(processed_samples):
            seq_len = sample['input_ids'].shape[0]
            input_ids[i, :seq_len] = sample['input_ids']
            attention_mask[i, :seq_len] = sample['attention_mask']
        
        # Create labels with masking
        labels = input_ids.clone()

        # CRITICAL: Focus training on </response> and </silence> tokens
        # Mask everything except the </response> or </silence> tokens at the end
        for i in range(batch_size):
            answer_text = answer_texts[i]

            # Find the actual sequence length (excluding padding)
            seq_length = attention_mask[i].sum().item()

            # Mask everything first
            labels[i, :] = -100

            # Determine which end token to look for
            if answer_text.strip().endswith("</response>"):
                end_token_str = "</response>"
            elif answer_text.strip().endswith("</silence>"):
                end_token_str = "</silence>"
            else:
                # No recognized end token, keep everything masked
                print(f"Warning: Sample {i} does not end with </response> or </silence>")
                continue

            # Tokenize the full answer to get the exact tokens as they appear in context
            # This handles context-dependent tokenization correctly
            full_answer_with_end = self.processor.tokenizer(
                f"{answer_text}<|im_end|>",
                add_special_tokens=False,
                return_tensors="pt"
            )['input_ids'][0]

            # Also tokenize just the end token string to find it in the answer
            end_token_alone = self.processor.tokenizer(
                end_token_str,
                add_special_tokens=False,
                return_tensors="pt"
            )['input_ids'][0]

            # Find where the end token appears in the full answer by searching backwards
            # We need to find the position where the token sequence matches
            found = False
            for search_pos in range(len(full_answer_with_end) - len(end_token_alone), -1, -1):
                # Check if the tokens match at this position
                if torch.equal(full_answer_with_end[search_pos:search_pos+len(end_token_alone)], end_token_alone):
                    # Found it! Calculate the position in the actual input_ids
                    # The answer starts at (seq_length - len(full_answer_with_end))
                    answer_start_in_input = seq_length - len(full_answer_with_end)
                    end_token_start = answer_start_in_input + search_pos
                    end_token_end = end_token_start + len(end_token_alone)

                    # Unmask only these tokens
                    labels[i, end_token_start:end_token_end] = input_ids[i, end_token_start:end_token_end]
                    found = True

                    # Debug: print first sample to verify
                    if i == 0:
                        print(f"[DEBUG] Sample 0: seq_len={seq_length}, end_token='{end_token_str}', "
                              f"end_token_length={len(end_token_alone)}, unmasked_range=[{end_token_start}:{end_token_end}]")
                        # Decode to verify
                        unmasked_text = self.processor.tokenizer.decode(input_ids[i, end_token_start:end_token_end])
                        print(f"[DEBUG] Unmasked text: '{unmasked_text}'")
                    break

            if not found:
                print(f"Warning: Sample {i} could not find {end_token_str} in tokenized sequence")

            # Verify that we have some unmasked tokens
            answer_token_count = (labels[i] != -100).sum().item()
            if answer_token_count == 0:
                print(f"Warning: Sample {i} has no unmasked tokens!")
        
        # Prepare output batch
        batch = {
            'input_ids': input_ids,
            'attention_mask': attention_mask.long(),
            'labels': labels,
        }
        
        # Concatenate all pixel_values and image_grid_thw
        if all_pixel_values:
            pixel_values = torch.cat(all_pixel_values, dim=0)
            batch['pixel_values'] = pixel_values
            
            if all_image_grid_thw:
                image_grid_thw = torch.cat(all_image_grid_thw, dim=0)
                batch['image_grid_thw'] = image_grid_thw
        
        return batch


def load_data(args, oversample_nil_factor: int = 5):
    """Load training and validation data from single JSON file using split key"""
    print(f"Loading data from {args.data_file}...")
    
    with open(args.data_file) as f:
        all_data = json.load(f)
    
    print(f"Loaded {len(all_data)} total samples")
    
    # Filter by split key
    train_questions = [q for q in all_data if q.get('split') == args.train_split_name]
    val_questions = [q for q in all_data if q.get('split') == args.val_split_name]
    print(f"Training samples (split='{args.train_split_name}'): {len(train_questions)}")
    print(f"Validation samples (split='{args.val_split_name}'): {len(val_questions)}")
    
    if len(train_questions) == 0:
        raise ValueError(f"No training samples found with split='{args.train_split_name}'!")
    
    # Ensure each sample has a unique ID
    for i, sample in enumerate(train_questions):
        if 'id' not in sample:
            video_id = sample.get('video_id', 'unknown')
            timestamp = sample.get('timestamp', '0')
            sample['id'] = f"{video_id}_{timestamp}_{i}"
    
    for i, sample in enumerate(val_questions):
        if 'id' not in sample:
            video_id = sample.get('video_id', 'unknown')
            timestamp = sample.get('timestamp', '0')
            sample['id'] = f"{video_id}_{timestamp}_{i}"
    
    # === Oversample non-NIL examples ===
    non_silent_samples = [q for q in train_questions if q.get('answer', '').strip() != "</silence>"]
    silent_samples = [q for q in train_questions if q.get('answer', '').strip() == "</silence>"]
    sampled_non_silent = random.choices(non_silent_samples, k=100)
    sampled_silent = random.choices(silent_samples, k=100)
    train_questions = sampled_non_silent + sampled_silent

    return train_questions, val_questions if len(val_questions) > 0 else None



def setup_model_and_tokenizer(args):
    """Load model, processor, and apply LoRA"""
    
    print(f"Loading model from {args.model_path}...")
    
    # Load model in fp16/bf16 for memory efficiency
    model = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(
        args.model_path,
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        trust_remote_code=True,
    )
    
    # Load processor
    processor = Qwen2_5OmniProcessor.from_pretrained(
        args.model_path,
        trust_remote_code=True
    )

    # Note: </response> and </silence> tokens are used in the dataset formatting
    # (see VideoMemoryQADataset.__getitem__ lines 207-217)
    # The tokenizer will handle them as regular text sequences - no need to add as special tokens
    # This avoids embedding resizing issues with LoRA
    print("[OK] Using </response> and </silence> as text sequences (no special token addition)")

    # Enable gradient checkpointing if requested
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        print("[OK] Gradient checkpointing enabled")

    # Configure LoRA
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=args.lora_target_modules,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )

    print(f"Applying LoRA with config: r={args.lora_r}, alpha={args.lora_alpha}")
    model = get_peft_model(model, lora_config)
    
    # Print trainable parameters
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    print(f"Trainable params: {trainable_params:,} ({100*trainable_params/total_params:.2f}%)")
    print(f"Total params: {total_params:,}")
    
    return model, processor


def main():
    args = parse_args()
    
    # Set random seed
    transformers.set_seed(args.seed)
    
    # Load model and processor
    model, processor = setup_model_and_tokenizer(args)
    
    # Load data
    train_questions, val_questions = load_data(args)
    
    # Create datasets
    print("Creating training dataset...")
    train_dataset = VideoMemoryQADataset(
        questions=train_questions,
        video_dir=args.video_dir,
        processor=processor,
        model_config=model.config,
        args=args,
        memory_cache=args.memory_cache
    )

    eval_dataset = None
    if val_questions is not None and len(val_questions) > 0:
        print("Creating validation dataset...")
        eval_dataset = VideoMemoryQADataset(
            questions=val_questions[:100],
            video_dir=args.video_dir,
            processor=processor,
            model_config=model.config,
            args=args,
            memory_cache=args.memory_cache
        )
    
    # Create data collator
    data_collator = VideoMemoryDataCollator(
        processor=processor,
        padding=True,
        max_length=2048,  # Adjust based on your needs
    )
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.num_train_epochs,
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        max_grad_norm=args.max_grad_norm,
        lr_scheduler_type=args.lr_scheduler_type,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        eval_steps=args.eval_steps,
        save_total_limit=args.save_total_limit,
        bf16=args.bf16,
        fp16=args.fp16,
        optim=args.optim,
        gradient_checkpointing=args.gradient_checkpointing,
        dataloader_num_workers=args.num_workers,
        remove_unused_columns=False,
        report_to=["tensorboard"],
        logging_dir=os.path.join(args.output_dir, "logs"),
        save_strategy="steps",
        eval_strategy="steps",
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        # DeepSpeed configuration
        deepspeed=args.deepspeed if args.deepspeed else None,
        # Remove or set to False when using DeepSpeed
        ddp_find_unused_parameters=False if args.deepspeed else True,
    )
    
    # Initialize trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator
    )
    
    # Train
    print("\n" + "="*50)
    print("Starting training...")
    print("="*50 + "\n")
    
    train_result = trainer.train()
    eval_metrics = trainer.evaluate()
    print(eval_metrics)

    # === Merge LoRA weights into base model ===
    print("\nMerging LoRA weights into base model...")
    try:
        model = model.merge_and_unload()
        print("[OK] Successfully merged LoRA weights into the base model")
    except Exception as e:
        print(f"[WARNING] Warning: Could not merge LoRA weights automatically. Error: {e}")

    
    # Save final model
    if args.local_rank == 0:
        print("\nSaving merged model and processor...")
        model.save_pretrained(args.output_dir)
        processor.save_pretrained(args.output_dir)

    
    # Save training metrics
    metrics = train_result.metrics
    trainer.log_metrics("train", metrics)
    trainer.save_metrics("train", metrics)
    trainer.save_state()
    
    print(f"\n[OK] Training complete! Model saved to {args.output_dir}")


if __name__ == "__main__":
    main()