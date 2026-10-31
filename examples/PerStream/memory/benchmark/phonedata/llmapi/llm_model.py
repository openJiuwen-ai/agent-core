"""
Text LLM Model 调用函数
用于调用 qwen3-235b 模型生成问题
"""
import json
import requests
import time
import os

os.environ["no_proxy"] = "localhost,127.0.0.1,.huawei.com"

def temp_sleep(seconds=0.1):
    time.sleep(seconds)

# API配置
API_URL = ""
API_KEY = "Bearer "
MODEL_TEXT = "qwen3-235b"

def call_llm_model(system_prompt, user_prompt, max_retries=3):
    """
    调用Text模型生成问题
    
    Args:
        system_prompt (str): 系统提示（强制第一人称要求）
        user_prompt (str): 用户提示/问题
        max_retries (int): 最大重试次数（已废弃，使用while True）
    
    Returns:
        dict: {question, answer, reasoning} 或 None
    """
    temp_sleep()
    
    json_data = {
        "model": MODEL_TEXT,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 2048,
        "temperature": 0.7
    }
    
    headers = {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
    }
    
    while True:
        try:
            response = requests.post(API_URL, headers=headers, json=json_data, verify=False, timeout=60)
            
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
                    except (KeyError, IndexError, TypeError):
                        print("Error accessing content")
                        return None
                    
                    # 解析JSON响应
                    import re
                    json_match = re.search(r'\{[^{}]*"question"[^{}]*"answer"[^{}]*"reasoning"[^{}]*\}', content, re.DOTALL)
                    if json_match:
                        result = json.loads(json_match.group())
                        # 移除options字段，将选项嵌入question
                        if "options" in result:
                            options = result["options"]
                            question_lines = [result.get("question", "")]
                            question_lines.append("\\nOptions:")
                            for label in ['A', 'B', 'C', 'D']:
                                if label in options:
                                    question_lines.append(f"{label}: {options[label]}")
                            result["question"] = "\\n".join(question_lines)
                            del result["options"]
                        return result
                    
                    # 尝试查找完整JSON
                    start = content.find('{')
                    end = content.rfind('}')
                    if start != -1 and end != -1:
                        try:
                            return json.loads(content[start:end+1])
                        except:
                            pass
                    
                    return None
                else:
                    print("The parsed JSON is not a dictionary")
                    return None
            
            else:
                print(f"Unhandled status code {response.status_code}: {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"[ERROR] LLM Exception: {e}")
            return None


# 测试
if __name__ == "__main__":
    # 简单测试
    system = "You are an expert assistant."
    prompt = "Say \'Hello\'"
    
    print("LLM model function ready")
    result = call_llm_model(system, prompt)
    print(f"Test result: {result}")
    
    print(f"\nAPI URL: {API_URL}")
    print(f"Model: {MODEL_TEXT}")