import torch
import torch.nn as nn
from transformers import Qwen2_5OmniProcessor, Qwen2_5OmniThinkerForConditionalGeneration, AutoModelForCausalLM, AutoProcessor

def load_model(model_path, device='cuda', model_type='qwen'):
    """Load model"""
    if model_type == 'qwen':
        processor = Qwen2_5OmniProcessor.from_pretrained(model_path, trust_remote_code=True)
        model = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(model_path, trust_remote_code=True)
    else:
        model = AutoModelForCausalLM.from_pretrained(model_path, trust_remote_code=True)
        processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    
    model = model.to(device)
    model.eval()
    
    print(f"Loaded model from {model_path}")
    return model, processor

class SpatialPreservingProjection(nn.Module):
    """
    Project each patch independently, preserving spatial structure.
    Input: (batch, num_patches, 3584)
    Output: (batch, num_patches, 384) for storage, (batch, 384) for comparison
    """
    def __init__(self, input_dim=3584, hidden_dim=1024, output_dim=384):
        super().__init__()
        
        # Shared projection applied to each patch independently
        self.patch_projection = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.LayerNorm(hidden_dim // 2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(hidden_dim // 2, output_dim),
        )
        
    def forward(self, x):
        """
        Args:
            x: [batch, num_patches, input_dim] e.g., [B, 16, 3584]
        Returns:
            patch_embeddings: [batch, num_patches, output_dim] - spatially aware features
            aggregated: [batch, output_dim] - for similarity comparison
        """
        batch_size, num_patches, input_dim = x.shape
        
        # Reshape to process all patches at once
        x_flat = x.reshape(-1, input_dim)
        
        # Project each patch
        projected_flat = self.patch_projection(x_flat)
        
        # Reshape back to [B, num_patches, 384]
        patch_embeddings = projected_flat.reshape(batch_size, num_patches, -1)
        
        # Average across patches for comparison (AFTER projection)
        aggregated = patch_embeddings.mean(dim=1)  # [B, 384]
        
        return aggregated

def load_projection_model(model_path, device='cuda', get_metadata=False):
    """Load trained projection model"""
    checkpoint = torch.load(model_path, map_location=device)
    metadata = checkpoint['metadata']
    
    model = SpatialPreservingProjection(
        input_dim=metadata['vision_dim'],
        output_dim=metadata['text_dim']
    ).to(device)
    
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()
    
    print(f"Loaded model from epoch {checkpoint['epoch']} with val_loss={checkpoint['val_loss']:.4f}")
    
    if get_metadata:
        return model, metadata
    else:
        return model
