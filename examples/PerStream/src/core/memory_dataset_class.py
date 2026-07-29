from torch.utils.data import Dataset, DataLoader
import random
import os
import torch
import numpy as np

class MemoryDataset(Dataset):
    """Dataset for memory-based video QA evaluation"""
    
    def __init__(self, questions, video_dir, processor, model_config, args, max_memory_length=512):
        self.questions = questions
        self.video_dir = video_dir
        self.processor = processor
        self.model_config = model_config
        self.args = args

        # Video processing parameters
        self.max_frames = args.max_frames
        self.image_size = args.image_size
        self.video_extensions = args.video_extensions
        self.video_start_before = args.video_start_before
        self.video_end_after = args.video_end_after
        self.target_fps = args.target_fps
        self.low_memory = args.low_memory
        
        # Memory-specific parameters
        self.memory_types = args.memory_types
        self.memory_format = args.memory_format
        self.memory_strategy = args.memory_strategy
        self.max_memory_length = max_memory_length


    def _load_video_frames(self, video_id, timestamp=None):
        """
        Load video frames from file
        
        Args:
            video_id: Path format like "vp15/run1_2018-05-30-13-05-35.kinect_color"
            timestamp: Float timestamp in seconds
        """
        for ext in self.video_extensions:
            video_path = os.path.join(self.video_dir, video_id + ext)
            if os.path.exists(video_path):
                frames = self._extract_frames(video_path, timestamp)
                return frames
        
        raise FileNotFoundError(f"Video file not found for: {video_id}")
    
    def _extract_frames(self, video_path, timestamp=None):
        """Extract frames from video file using torchvision with memory optimization"""
        try:
            import torchvision.io
            from PIL import Image
        except ImportError:
            raise ImportError("torchvision and PIL are required for video processing. Install with: pip install torchvision pillow")
        
        try:
            if timestamp is not None:
                # Extract frames around a specific timestamp
                start_time = max(0, timestamp - self.video_start_before)
                end_time = timestamp + self.video_end_after
                frames, _, info = torchvision.io.read_video(
                    video_path, 
                    start_pts=start_time, 
                    end_pts=end_time, 
                    pts_unit="sec"
                )
            else:
                # Extract frames from entire video
                frames, _, info = torchvision.io.read_video(video_path, pts_unit="sec")
            
            if frames.size(0) == 0:
                raise ValueError(f"No frames loaded from video: {video_path}")
            
            # More aggressive frame sampling for memory efficiency
            native_fps = info.get("video_fps", 30.0)
            stride = max(int(round(native_fps / self.target_fps)), 2)  # Minimum stride of 2
            
            # Apply stride sampling
            sampled_frames = frames[::stride]
            
            # If we still have too many frames, sample evenly
            if sampled_frames.size(0) > self.max_frames:
                frame_indices = torch.linspace(0, sampled_frames.size(0)-1, self.max_frames).long()
                sampled_frames = sampled_frames[frame_indices]
            
            # Process frames one by one to save memory
            processed_frames = []
            for i in range(sampled_frames.size(0)):
                frame = sampled_frames[i]  # Shape: (H, W, C)

                # Convert to PIL Image for resizing
                # Handle bfloat16 - convert to float32 first as numpy doesn't support bfloat16
                if frame.dtype == torch.bfloat16:
                    frame = frame.float()
                frame_np = frame.numpy()
                # Ensure values are in [0, 255] range for uint8 conversion
                if frame_np.max() <= 1.0:
                    frame_np = (frame_np * 255).astype(np.uint8)
                else:
                    frame_np = frame_np.astype(np.uint8)
                frame_pil = Image.fromarray(frame_np)
                
                # Resize to smaller size if in low memory mode
                target_size = self.image_size // 2 if self.low_memory else self.image_size
                frame_pil = frame_pil.resize((target_size, target_size))
                
                # Convert back to tensor
                frame_tensor = torch.from_numpy(np.array(frame_pil)).float()
                processed_frames.append(frame_tensor)
                
                # Clear intermediate tensors
                del frame
                
            if not processed_frames:
                raise ValueError(f"No frames processed from video: {video_path}")
            
            # Clear original frames tensor
            del frames, sampled_frames
            
            # Stack and format frames
            frames_tensor = torch.stack(processed_frames)  # Shape: (num_frames, H, W, C)
            frames_tensor = frames_tensor.permute(0, 3, 1, 2)  # Shape: (num_frames, C, H, W)
            
            # Clear processed_frames list
            del processed_frames
            
            # Normalize to [0, 1]
            # frames_tensor = frames_tensor / 255.0
            return frames_tensor
            
        except Exception as e:
            raise ValueError(f"Error processing video {video_path}: {str(e)}")

    def __getitem__(self, index):
        sample = self.questions[index]
        video_id = sample['video_id']
        
        try:
            # Parse timestamp (stored as string in new format)
            timestamp = sample.get('timestamp', None)
            if timestamp is not None and isinstance(timestamp, str):
                try:
                    timestamp = float(timestamp)
                except ValueError:
                    print(f"Warning: Invalid timestamp '{timestamp}' for video {video_id}, ignoring timestamp")
                    timestamp = None
            
            video_tensor = self._load_video_frames(video_id, timestamp)
            
        except Exception as e:
            print(f'Dataset Exception: {e}, video_id: {video_id}, randomly choose one.')
            idx = random.randint(0, len(self.questions) - 1)
            return self.__getitem__(idx)
        
        # Format question with memory context if enabled
        if sample.get('type') == 'proactive':
            user_prompt = "Please answer as if you are a personal assistant. No need to say your reasoning or if it is based on what memories, but be a helpful assistant. "
            qs = user_prompt + "\n\nQuestion: " + "Give a proactive response to the user's question based on the memories and video. Output </silence> if you don't have a response and </response> if you have a response."
        else:
            user_prompt = "Please answer as if you are a personal assistant. No need to say your reasoning or if it is based on what memories, but be a helpful assistant. "
            # Handle cases where 'question' might not exist (use 'caption' as fallback)
            question_text = sample.get('question', sample.get('caption', ''))
            qs = user_prompt + "\n\nQuestion: " + question_text
        
        memories_list = sample.get('type_1_memories', []) + sample.get('type_2_memories', [])
        return {
            'memories': memories_list,
            'video_tensor': video_tensor,
            'video_id': video_id,
            'timestamp': sample.get('timestamp', None),
            'question': qs,
            'answer': sample.get('answer', ''),
            'sample_id': f"{video_id}_{timestamp}"
        }

    def __len__(self):
        return len(self.questions)