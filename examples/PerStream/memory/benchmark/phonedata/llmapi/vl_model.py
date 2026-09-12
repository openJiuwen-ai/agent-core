"""
Vision-Language Model 调用函数
用于调用 qwen35-35b-vl 模型分析图片并生成问题
"""
import json
import requests
import time
import base64

# API配置
API_URL = ""
API_KEY = "Bearer"
MODEL_VL = "qwen35-35b-vl"

def call_vl_model(system_prompt, user_content, image_path, max_retries=3):
    """
    调用Vision-Language模型分析图片
    
    Args:
        system_prompt (str): 系统提示（强制第一人称要求）
        user_content (str): 用户提示/问题
        image_path (str): 图片文件路径
        max_retries (int): 最大重试次数
    
    Returns:
        dict: {question, answer, reasoning} 或 None
    """
    import os
    
    if not os.path.exists(image_path):
        print(f"[ERROR] Image not found: {image_path}")
        return None
    
    # 读取并编码图片
    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        print(f"[ERROR] Failed to read image: {e}")
        return None
    
    # 重试循环
    for attempt in range(max_retries):
        try:
            time.sleep(0.5)
            
            json_data = {
                "model": MODEL_VL,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                            {"type": "text", "text": user_content}
                        ]
                    }
                ],
                "max_tokens": 2048
            }
            
            headers = {
                "Authorization": API_KEY,
                "Content-Type": "application/json",
            }
            
            response = requests.post(API_URL, headers=headers, json=json_data, verify=False, timeout=60)
            
            if response.status_code == 429:
                wait_time = 30 * (attempt + 1)
                print(f"[VL] Rate limited, waiting {wait_time}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue
            
            if response.status_code != 200:
                print(f"[ERROR] VL API error {response.status_code}: {response.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None
            
            data = json.loads(response.text)
            content = data["choices"][0]["message"]["content"]
            
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
            
            # 如果没有找到标准JSON格式，返回分析结果作为文本
            return {"answer": content, "question": "", "reasoning": ""}
            
        except Exception as e:
            print(f"[ERROR] VL exception (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
    
    print(f"[ERROR] VL failed after {max_retries} attempts")
    return None


# 测试
if __name__ == "__main__":
    # 简单测试
    print("VL model function ready")
    print(f"API URL: {API_URL}")
    print(f"Model: {MODEL_VL}")