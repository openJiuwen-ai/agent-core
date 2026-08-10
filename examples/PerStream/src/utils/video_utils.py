import torch
import torch.nn.functional as F
from PIL import Image
from transformers import CLIPImageProcessor
import cv2
from tqdm import tqdm

def process_images(images, image_processor):
    new_images = []
    for image in images:
        image = image_processor(image, return_tensors="pt")["pixel_values"][0]
        new_images.append(image) # [ dim resize_w resize_h ]
    if len(images) > 1:
        new_images = [torch.stack(new_images, dim=0)] # [num_images dim resize_w resize_h ]
    if all(x.shape == new_images[0].shape for x in new_images):
        new_images = torch.stack(new_images, dim=0) # num_image num_patches dim resize_w resize_h
        # when using "pad" mode and only have one image the new_images tensor dimension is [ 1 dim resize_w resize_h ]
    
    return new_images

def compute_gradients(img):
    sobel_x = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=torch.float32, device=img.device).unsqueeze(0).unsqueeze(0)
    sobel_y = torch.tensor([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=torch.float32, device=img.device).unsqueeze(0).unsqueeze(0)
    Ix = F.conv2d(img.unsqueeze(0), sobel_x, padding=1)
    Iy = F.conv2d(img.unsqueeze(0), sobel_y, padding=1)
    return Ix.squeeze(0), Iy.squeeze(0)

def calculate_optical_flow(last_frame, current_frame, image_processor, threshold, device):
    window_size, eps = 5, 1e-6
    assert last_frame is not None
    current_image_tensor = process_images([Image.fromarray(current_frame)], image_processor).to(device).squeeze(0)
    last_image_tensor = process_images([Image.fromarray(last_frame)], image_processor).to(device).squeeze(0)
    if current_image_tensor.dim() == 3:
        current_image_tensor_gray = 0.2989 * current_image_tensor[0, :, :] + 0.5870 * current_image_tensor[1, :, :] + 0.1140 * current_image_tensor[2, :, :]
        last_image_tensor_gray = 0.2989 * last_image_tensor[0, :, :] + 0.5870 * last_image_tensor[1, :, :] + 0.1140 * last_image_tensor[2, :, :]
    else:
        current_image_tensor_gray = current_image_tensor
        last_image_tensor_gray = last_image_tensor

    # Compute gradients on GPU
    Ix, Iy = compute_gradients(last_image_tensor_gray.unsqueeze(0))
    It = current_image_tensor_gray - last_image_tensor_gray

    # Prepare for batch processing
    Ix_windows = F.unfold(Ix.unsqueeze(0), kernel_size=(window_size, window_size)).transpose(1, 2)
    Iy_windows = F.unfold(Iy.unsqueeze(0), kernel_size=(window_size, window_size)).transpose(1, 2)
    It_windows = F.unfold(It.unsqueeze(0).unsqueeze(0), kernel_size=(window_size, window_size)).transpose(1, 2)
    A = torch.stack((Ix_windows, Iy_windows), dim=3)
    b = -It_windows

    # Using Lucas-Karthy method
    # Reshape to (batch_size, num_windows, window_size*window_size, 2)
    A = A.view(A.size(0), -1, window_size*window_size, 2)
    b = b.view(b.size(0), -1, window_size*window_size)

    # Compute A^T * A and A^T * b
    A_T_A = torch.matmul(A.transpose(2, 3), A)
    A_T_b = torch.matmul(A.transpose(2, 3), b.unsqueeze(3)).squeeze(3)

    # Add regularization term to A_T_A
    eye = torch.eye(A_T_A.size(-1), device=A_T_A.device)
    A_T_A += eps * eye

    # Solve for flow vectors in batch
    nu = torch.linalg.solve(A_T_A, A_T_b)

    u_flat = nu[:, :, 0]
    v_flat = nu[:, :, 1]

    # Reshape flow vectors to image shape
    # Calculate correct output size for fold
    output_height = Ix.shape[1] - window_size + 1
    output_width = Ix.shape[2] - window_size + 1

    # Ensure the data is suitable for fold operation
    u_flat = u_flat.view(1, output_height,  output_width)
    v_flat = v_flat.view(1, output_height,  output_width)

    # Compute magnitude of flow vectors
    mag = torch.sqrt(u_flat**2 + u_flat**2)
    mean_mag = mag.mean().item()

    del Ix_windows, Iy_windows, It_windows
    del current_image_tensor_gray, last_image_tensor_gray, last_image_tensor

    return mean_mag > threshold, mean_mag, current_image_tensor

last_frame = None
def optical_flow_keyframe_sampling(frames, optical_flow_threshold: float = 0.5, device: str = "cuda"):
    image_processor = CLIPImageProcessor()
    global last_frame
    selected_frames = []
    selected_ids = []
    for idx, current_frame in enumerate(frames):
        if idx == 0:
            selected_frames.append(Image.fromarray(current_frame))
            selected_ids.append(idx)
            continue

        if last_frame is None:
            last_frame = current_frame
            continue

        with torch.no_grad():
            is_change, _, _ = calculate_optical_flow(last_frame, current_frame, image_processor, optical_flow_threshold, device)
        torch.cuda.empty_cache()
        last_frame = current_frame
        if is_change:
            selected_frames.append(Image.fromarray(current_frame))
            selected_ids.append(idx)
    return selected_frames, selected_ids

def process_video(video_path, total_frames=2000, sample_rate=16, target_height=224):
    # Extract all frames first
    print("Video path:", video_path)
    cap = cv2.VideoCapture(video_path)
    all_frames_raw = []

    if not cap.isOpened():
        raise ValueError(f"Failed to open video file: {video_path}. Check if the file is corrupted or if OpenCV supports this codec.")

    # Get original video width and height to preserve aspect ratio
    original_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    original_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"W:{original_width}, H:{original_height}")
    aspect_ratio = original_width / original_height
    target_width = int(target_height * aspect_ratio)

    for i in tqdm(range(0, total_frames, sample_rate)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            # Convert BGR -> RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Resize to 480p (keeping aspect ratio)
            frame_rgb = cv2.resize(frame_rgb, (target_width, target_height), interpolation=cv2.INTER_AREA)
            all_frames_raw.append(frame_rgb)
    cap.release()

    # Perform optical flow keyframe sampling
    all_frames, selected_ids = optical_flow_keyframe_sampling(all_frames_raw)
    selected_ids = [sample_rate * idx for idx in selected_ids]
    print(f"Sampled {len(all_frames)}/{len(all_frames_raw)} frames, applying memory-based selection...")

    return all_frames, selected_ids
