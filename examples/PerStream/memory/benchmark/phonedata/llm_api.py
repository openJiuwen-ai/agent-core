import requests
import json
import time
from typing import Dict, Any, Optional
import os

os.environ["no_proxy"] = "localhost,127.0.0.1,.huawei.com"

def temp_sleep(seconds=0.1):
    time.sleep(seconds)

API_KEY = "Bearer"
API_URL = ""
# MODEL_TEXT = "qwen3-235b"
# MODEL_TEXT = "minimax-m27"
MODEL_TEXT = "qwen36-27b-vl"
def call_text_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:
    temp_sleep()
    
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": MODEL_TEXT,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    
    while True:
        try:
            response = requests.post(API_URL, headers=headers, json=payload, verify=False, timeout=250)
            
            if response.status_code == 429:
                print("Received 429 Too Many Requests, waiting 30 seconds...")
                time.sleep(30)
                continue
            
            elif response.status_code == 200:
                json_str = response.text
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    print("Error decoding JSON")
                    return None
                
                if isinstance(data, dict):
                    try:
                        content = data["choices"][0]["message"]["content"]
                        return content
                    except (KeyError, IndexError, TypeError):
                        print("Error accessing content")
                        return None
                else:
                    print("The parsed JSON is not a dictionary")
                    return None
            
            else:
                print(f"Unhandled status code {response.status_code}: {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"Exception: {e}")
            return None


a = "hi"
b ="hi"

c = call_text_llm(a,b)
print(c)