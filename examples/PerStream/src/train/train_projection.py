#!/usr/bin/env python3
"""
Train MLP projection that preserves spatial information.
Projects each patch independently: (num_patches, 3584) -> (num_patches, 384)
Then averages for comparison: (num_patches, 384) -> (384)

Modified to extract frames from videos at timestamps instead of using image directories.
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import numpy as np
from pathlib import Path
import json
from tqdm import tqdm
import argparse
from sentence_transformers import SentenceTransformer
import torchvision.io
from PIL import Image
import os
import random
from src.utils.perstream_utils import get_image_embedding, generate_buffer_caption
from src.utils.model_utils import load_model, load_projection_model, SpatialPreservingProjection


class AlignmentDataset(Dataset):
    """Dataset of (vision_embedding, caption, sentence_embedding) triplets"""
    def __init__(self, data_file):
        self.data = torch.load(data_file, weights_only=False)
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        item = self.data[idx]
        return {
            'vision_emb': torch.FloatTensor(item['vision_emb']),  # [num_patches, 3584]
            'text_emb': torch.FloatTensor(item['text_emb'])  # [384]
        }


def extract_frame_at_timestamp(video_path, timestamp, image_size=224, 
                               video_start_before=1.0, video_end_after=1.0):
    """
    Extract a single frame from video at specified timestamp.
    
    Args:
        video_path: Path to video file
        timestamp: Timestamp in seconds
        image_size: Target image size
        video_start_before: Seconds before timestamp to start extraction
        video_end_after: Seconds after timestamp to end extraction
    
    Returns:
        PIL Image of the extracted frame
    """
    try:
        # Extract frames around timestamp
        start_time = max(0, timestamp - video_start_before)
        end_time = timestamp + video_end_after
        
        frames, _, info = torchvision.io.read_video(
            video_path, 
            start_pts=start_time, 
            end_pts=end_time, 
            pts_unit="sec"
        )
        
        if frames.size(0) == 0:
            raise ValueError(f"No frames loaded from video: {video_path}")
        
        # Take the middle frame (closest to target timestamp)
        mid_idx = frames.size(0) // 2
        frame = frames[mid_idx]  # Shape: (H, W, C)
        
        # Convert to PIL Image
        frame_pil = Image.fromarray(frame.numpy())
        frame_pil = frame_pil.resize((image_size, image_size))
        
        return frame_pil
        
    except Exception as e:
        raise ValueError(f"Error extracting frame from {video_path} at {timestamp}s: {str(e)}")


def load_video_samples_from_questions(questions_file, video_dir, video_extensions=['.mp4', '.avi', '.mkv'],
                                      max_samples=None, sample_strategy='random'):
    """
    Load video samples from questions file (similar to MemoryDataset).
    
    Args:
        questions_file: Path to JSON file with questions
        video_dir: Directory containing video files
        video_extensions: List of video file extensions
        max_samples: Maximum number of samples to extract
        sample_strategy: 'random' or 'sequential'
    
    Returns:
        List of (video_path, timestamp, question, answer) tuples
    """
    with open(questions_file, 'r') as f:
        questions = json.load(f)
    
    samples = []
    for sample in questions:
        video_id = sample['video_id']
        
        # Parse timestamp
        timestamp = sample.get('timestamp', None)
        if timestamp is not None and isinstance(timestamp, str):
            try:
                timestamp = float(timestamp)
            except ValueError:
                print(f"Warning: Invalid timestamp '{timestamp}' for video {video_id}, skipping")
                continue
        
        if timestamp is None:
            # Skip samples without timestamps or use middle of video
            continue
        
        # Find video file
        video_path = None
        for ext in video_extensions:
            candidate_path = os.path.join(video_dir, video_id + ext)
            if os.path.exists(candidate_path):
                video_path = candidate_path
                break
        
        if video_path is None:
            print(f"Warning: Video not found for {video_id}")
            continue
        
        samples.append({
            'video_path': video_path,
            'video_id': video_id,
            'timestamp': timestamp,
        })
    
    # Apply sampling strategy
    if max_samples and len(samples) > max_samples:
        if sample_strategy == 'random':
            samples = random.sample(samples, max_samples)
        else:  # sequential
            samples = samples[:max_samples]
    
    print(f"Loaded {len(samples)} video samples with timestamps")
    return samples


def create_alignment_dataset_from_videos(questions_file, video_dir, output_file, model_path,
                                        sentence_model_name='all-MiniLM-L6-v2', 
                                        pool_size=(4, 4), max_samples=None,
                                        image_size=224, video_extensions=['.mp4', '.avi', '.mkv'],
                                        video_start_before=0.5, video_end_after=0.5, save_every=100):
    """Create dataset of aligned vision and text embeddings from video timestamps."""
    # Load models
    print("Loading Qwen model...")
    model, processor = load_model(model_path, device='cuda')
    
    print(f"Loading sentence transformer: {sentence_model_name}")
    sentence_model = SentenceTransformer(sentence_model_name)
    
    # Load video samples
    print(f"Loading video samples from {questions_file}...")
    samples = load_video_samples_from_questions(
        questions_file, 
        video_dir, 
        video_extensions=video_extensions,
        max_samples=max_samples,
        sample_strategy='random'
    )
    
    print(f"Processing {len(samples)} video frames...")
    
    dataset = []
    for idx, sample in enumerate(tqdm(samples)):
        try:
            # Extract frame at timestamp
            image = extract_frame_at_timestamp(
                sample['video_path'],
                sample['timestamp'],
                image_size=image_size,
                video_start_before=video_start_before,
                video_end_after=video_end_after
            )
            
            # Get vision embedding
            vision_emb = get_image_embedding(image, model, processor, pool_size=pool_size)
            
            # Generate caption for the frame
            caption = generate_buffer_caption([image], model, processor)
            print("Caption:")
            print(caption)
            print("-----------------------------------")
            
            # Get text embedding from caption
            text_emb = sentence_model.encode(caption, convert_to_numpy=True)
            
            dataset.append({
                'video_id': sample['video_id'],
                'video_path': sample['video_path'],
                'timestamp': sample['timestamp'],
                'vision_emb': vision_emb,  # [num_patches, 3584]
                'text_emb': text_emb,  # [384]
            })
            
        except Exception as e:
            print(f"Error processing {sample['video_id']} at {sample['timestamp']}s: {e}")
            continue

        if idx % save_every == 0:
            # Save dataset
            print(f"Saving {len(dataset)} samples to {output_file}")
            torch.save(dataset, output_file)

    print(f"Saving {len(dataset)} samples to {output_file}")
    torch.save(dataset, output_file)
    
    # Save metadata
    metadata = {
        'num_samples': len(dataset),
        'sentence_model': sentence_model_name,
        'pool_size': pool_size,
        'vision_dim': dataset[0]['vision_emb'].shape[-1] if dataset else None,
        'text_dim': dataset[0]['text_emb'].shape[-1] if dataset else None,
        'image_size': image_size,
        'video_start_before': video_start_before,
        'video_end_after': video_end_after,
        'questions_file': questions_file,
    }
    with open(output_file.replace('.pt', '_metadata.json'), 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print("Dataset creation complete!")
    return metadata


def train_projection(data_file, output_model_path, batch_size=32, num_epochs=50, lr=1e-3, device='cuda'):
    """Train spatial-preserving projection layer."""
    # Load dataset
    print(f"Loading dataset from {data_file}")
    dataset = AlignmentDataset(data_file)
    
    # Load metadata
    with open(data_file.replace('.pt', '_metadata.json'), 'r') as f:
        metadata = json.load(f)
    
    print(f"Dataset: {len(dataset)} samples")
    print(f"Vision dim: {metadata['vision_dim']}, Text dim: {metadata['text_dim']}")
    
    # Split dataset
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = torch.utils.data.random_split(dataset, [train_size, val_size])
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=4)
    
    # Initialize model
    model = SpatialPreservingProjection(
        input_dim=metadata['vision_dim'],
        output_dim=metadata['text_dim']
    ).to(device)
    
    print(f"\nModel architecture:")
    print(model)
    print(f"Total parameters: {sum(p.numel() for p in model.parameters()):,}")
    
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs)
    criterion = nn.MSELoss()
    
    best_val_loss = float('inf')
    
    print("\nStarting training...")
    for epoch in range(num_epochs):
        # Training
        model.train()
        train_loss = 0
        for batch in tqdm(train_loader, desc=f"Epoch {epoch+1}/{num_epochs}"):
            vision_emb = batch['vision_emb'].to(device)  # [B, num_patches, 3584]
            text_emb = batch['text_emb'].to(device)  # [B, 384]
            
            optimizer.zero_grad()
            
            # Forward pass
            aggregated = model(vision_emb)
            
            # Loss on aggregated output (comparing to sentence embedding)
            loss = criterion(aggregated, text_emb)
            
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            train_loss += loss.item()
        
        train_loss /= len(train_loader)
        
        # Validation
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for batch in val_loader:
                vision_emb = batch['vision_emb'].to(device)
                text_emb = batch['text_emb'].to(device)
                
                aggregated = model(vision_emb)
                loss = criterion(aggregated, text_emb)
                val_loss += loss.item()
        
        val_loss /= len(val_loader)
        scheduler.step()
        
        print(f"Epoch {epoch+1}: Train Loss = {train_loss:.4f}, Val Loss = {val_loss:.4f}, LR = {scheduler.get_last_lr()[0]:.6f}")
        
        # Save best model
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save({
                'model_state_dict': model.state_dict(),
                'metadata': metadata,
                'epoch': epoch,
                'val_loss': val_loss,
                'train_loss': train_loss
            }, output_model_path)
            print(f"[OK] Saved best model (val_loss: {val_loss:.4f})")
    
    print(f"\nTraining complete! Best val loss: {best_val_loss:.4f}")
    print(f"Model saved to: {output_model_path}")

def test_projection_model(model, metadata, device='cuda'):
    """Test projection model with sample inputs to verify shapes"""
    print("\n" + "="*60)
    print("TESTING PROJECTION MODEL")
    print("="*60)
    
    # Get dimensions from metadata
    input_dim = metadata['vision_dim']
    output_dim = metadata['text_dim']
    pool_size = metadata.get('pool_size', [4, 4])
    num_patches = pool_size[0] * pool_size[1]
    
    print(f"\nModel configuration:")
    print(f"  Input dim: {input_dim}")
    print(f"  Output dim: {output_dim}")
    print(f"  Pool size: {pool_size}")
    print(f"  Num patches: {num_patches}")
    
    # Create sample batch
    batch_sizes = [1, 4, 8]
    
    for batch_size in batch_sizes:
        print(f"\nTesting batch_size={batch_size}:")
        
        # Create random input: [batch, num_patches, input_dim]
        sample_input = torch.randn(batch_size, num_patches, input_dim).to(device)
        print(f"  Input shape: {sample_input.shape}")
        
        # Forward pass
        with torch.no_grad():
            aggregated = model(sample_input)
        
        print(f"  Aggregated shape: {aggregated.shape} (for similarity)")
        
        # Verify shapes
        assert aggregated.shape == (batch_size, output_dim), \
            f"Expected aggregated shape {(batch_size, output_dim)}, got {aggregated.shape}"
        
        print(f"  [OK] Shapes verified!")
    
    print(f"\n{'='*60}")
    print("ALL TESTS PASSED [OK]")
    print("="*60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train vision-to-text embedding alignment from video timestamps")
    parser.add_argument("--mode", choices=['create_dataset', 'train', 'both', 'test'], default='both',
                       help="Mode: create dataset, train model, or both")
    parser.add_argument("--questions_file", type=str, required=True,
                       help="Path to questions JSON file with video_id and timestamps")
    parser.add_argument("--video_dir", type=str, required=True,
                       help="Directory containing video files")
    parser.add_argument("--dataset_file", type=str, default="alignment_dataset.pt",
                       help="Path to save/load dataset")
    parser.add_argument("--output_model", type=str, default="projection_mlp.pt",
                       help="Path to save trained model")
    parser.add_argument("--model_path", type=str,
                       default="Qwen/Qwen2.5-Omni-7B",
                       help="Path to Qwen model")
    parser.add_argument("--sentence_model", type=str, default="all-MiniLM-L6-v2",
                       help="Sentence transformer model name")
    parser.add_argument("--pool_size", type=int, default=4,
                       help="Pooling size (will use pool_size x pool_size)")
    parser.add_argument("--max_samples", type=int, default=None,
                       help="Max video samples to process (None = all)")
    parser.add_argument("--image_size", type=int, default=224,
                       help="Target image size for extracted frames")
    parser.add_argument("--video_start_before", type=float, default=2.0,
                       help="Seconds before timestamp to extract")
    parser.add_argument("--video_end_after", type=float, default=2.0,
                       help="Seconds after timestamp to extract")
    parser.add_argument("--video_extensions", nargs='+', default=['.mp4', '.avi', '.mkv'],
                       help="Video file extensions to search for")
    parser.add_argument("--batch_size", type=int, default=32,
                       help="Training batch size")
    parser.add_argument("--num_epochs", type=int, default=50,
                       help="Number of training epochs")
    parser.add_argument("--lr", type=float, default=1e-3,
                       help="Learning rate")
    
    args = parser.parse_args()
    
    if args.mode in ['create_dataset', 'both']:
        print("\n" + "="*60)
        print("CREATING ALIGNMENT DATASET FROM VIDEO TIMESTAMPS")
        print("="*60)
        metadata = create_alignment_dataset_from_videos(
            questions_file=args.questions_file,
            video_dir=args.video_dir,
            output_file=args.dataset_file,
            model_path=args.model_path,
            sentence_model_name=args.sentence_model,
            pool_size=(args.pool_size, args.pool_size),
            max_samples=args.max_samples,
            image_size=args.image_size,
            video_extensions=args.video_extensions,
            video_start_before=args.video_start_before,
            video_end_after=args.video_end_after
        )
    
    if args.mode in ['train', 'both']:
        print("\n" + "="*60)
        print("TRAINING PROJECTION MODEL")
        print("="*60)
        train_projection(
            data_file=args.dataset_file,
            output_model_path=args.output_model,
            batch_size=args.batch_size,
            num_epochs=args.num_epochs,
            lr=args.lr
        )

    model, metadata = load_projection_model(args.output_model, get_metadata=True)
    test_projection_model(model, metadata=metadata)