import requests
import json
import time
from typing import Dict, Any, List

API_KEY = "Bearer "
API_URL = ""
# MODEL_VL = "qwen35-35b-vl"
MODEL_VL = "qwen36-35b-vl"

def call_vl_model(system_prompt: str, encoded_image: str, user_content: str, max_tokens: int = 2048) -> str:
    """
    Call VL model with a single image
    
    Args:
        system_prompt (str): System prompt
        encoded_image (str): Base64 encoded image string
        user_content (str): User prompt
        max_tokens (int): Max tokens for response
    
    Returns:
        str: Model response content
    """
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL_VL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}},
                    {"type": "text", "text": user_content}
                ]
            }
        ],
        "max_tokens": max_tokens
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=250)
            
            if response.status_code == 429:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue
            
            if response.status_code != 200:
                raise Exception(f"API returned status {response.status_code}: {response.text}")
            
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue
            raise Exception(f"Request failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Error: {str(e)}")
    
    raise Exception(f"Max retries ({max_retries}) reached")


def call_vl_model_multiple(system_prompt: str, encoded_images: List[str], user_content: str, max_tokens: int = 2048) -> str:
    """
    Call VL model with multiple images in a single call (much faster)
    
    Args:
        system_prompt (str): System prompt
        encoded_images (list): List of base64 encoded image strings
        user_content (str): User prompt
        max_tokens (int): Max tokens for response
    
    Returns:
        str: Model response content
    """
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json"
    }
    
    # Build content list with multiple images
    content_list = []
    for encoded_image in encoded_images:
        content_list.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}
        })
    
    # Add text instruction
    content_list.append({
        "type": "text",
        "text": user_content
    })
    
    payload = {
        "model": MODEL_VL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": content_list
            }
        ],
        "max_tokens": max_tokens
    }
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=250)
            
            if response.status_code == 429:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue
            
            if response.status_code != 200:
                raise Exception(f"API returned status {response.status_code}: {response.text}")
            
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue
            raise Exception(f"Request failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Error: {str(e)}")
    
    raise Exception(f"Max retries ({max_retries}) reached")