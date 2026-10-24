import os
import random
from llm_api import call_text_llm
from vl_api import call_vl_model
import base64
from new_related import sim_output, get_goal_by_filename, extract_event_id_from_filename, collect_related_items

folder_path = "zipdata/aitw_images/single"

file_list = sorted([
    f for f in os.listdir(folder_path)
    if os.path.isfile(os.path.join(folder_path, f))
])[0:1581]

file_list1 = [f"zipdata/aitw_images/single/{name}" for name in file_list]


#无goal的提取event_list
def extract_event_list_nogoal(file_list1):
    event_list = {}    
    for item in file_list1:
        event_id, step, folder = extract_event_id_from_filename(item)
    
        key = event_id  
        if key not in event_list :
            event_list [key] = []
        event_list [key].append(item)
    return event_list




llm_system_prompt = """You are an action summarization assistant.

                        Your task is to infer the single main user action or goal from a sequence of input images. These images represent consecutive steps in one workflow, usually on a website, app, or digital interface.
                        
                        You must analyze the full sequence and produce exactly one concise English instruction that best summarizes the overall intended action or task.
                        
                        Requirements:
                        - Output only one sentence.
                        - Do not output any explanation, reasoning, prefix, suffix, label, bullet point, or quotation marks.
                        - The sentence must describe the main operation or goal shown across the image sequence.
                        - Prefer an imperative instruction style.
                        - Keep it specific to the actual site, platform, product, or action when visible.
                        - If the sequence shows multiple small steps, summarize them into one higher-level instruction.
                        - If the sequence clearly includes a final outcome, include it in the instruction.
                        - Focus on the user's intent, not on low-level visual details.
                        - Do not mention “image”, “screenshot”, “sequence”, or “picture”.
                        - Focus on process-based operations and do not pay attention to unclear descriptions.
                        - Do not list multiple options.
                        - Do not use vague statements like “interact with the page” or “perform the task”.
                        - The output must be natural, direct, and actionable.
                        
                        Good output examples:
                        Go to the search bar on Target and search for USB.
                        Search Bose SoundLink on Amazon.co.uk.
                        Choose the first search result and add it to the cart.
                        Open eBay.com.
                        Open the first search result for AirPods on eBay, review the product details, and add it to the cart.
                        
                        Now analyze the provided image sequence and output exactly one sentence in English.
                        """


vl_sys_prompt = """You are an expert at analyzing mobile phone screenshots.

            First, determine what type of page this screenshot shows.
            Then, describe what content is visible on the page.
            
            Output format:
            - What this page is for:
            - Main visible content:
            - Page structure:
            - Notable details:
            
            Instructions:
            - Identify the most likely page type based only on visible evidence.
            - Summarize the main content shown on the screen, including text, buttons, images, sections, tabs, menus, and notifications.
            - Describe the layout from top to bottom.
            - Do not make up details that are not visible.
            - If anything is blurry or unreadable, explicitly say so.
            """


def ana_pic(sim_out):
    results = []

    for i in sim_out:
        try:
            img = i
            enc = base64.b64encode(open(img, 'rb').read()).decode('utf-8')
            desc = call_vl_model(
                vl_sys_prompt,
                enc,
                "Describe this picture in detail.",
                max_tokens=700
            )

            name = i.split("/")[-1].replace(".png", "")
            a, b = name.rsplit("_", 1)
            new_item = [a, b, desc]
            results.append(new_item)

        except Exception as e:
            print(f"Error processing {i}: {e}")

    return results

import requests
import json
import time
from typing import Dict, Any, Optional
import os

os.environ["no_proxy"] = "localhost,127.0.0.1,.huawei.com"

def temp_sleep(seconds=0.1):
    time.sleep(seconds)

API_KEY = "Bearer "
API_URL = ""
# MODEL_TEXT = "qwen3-235b"
MODEL_TEXT = "minimax-m27"
# MODEL_TEXT = "DeepSeek-V4-Flash-W8A8"

def call_text_llm2(system_prompt: str, user_prompt: str, max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:
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
            response = requests.post(API_URL, headers=headers, json=payload, verify=False, timeout=120)
            
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


import re

def sort_image_list(file_list):
    def extract_index(path):
        match = re.search(r'_(\d+)\.png$', path)
        return int(match.group(1)) if match else float('inf')

    return sorted(file_list, key=extract_index)

def gene_goal(file_list):
    event_list = extract_event_list_nogoal(file_list)
    output = []

    for event_id in event_list:
        sorted_list = sort_image_list(event_list[event_id])
        pic_con = ana_pic(sorted_list)
        result = call_text_llm2(
            system_prompt=llm_system_prompt,
            user_prompt=str(pic_con)
        )
        print(result)

        # 把原来的图片列表改成一个更完整的结构
        event_list[event_id] = {
            "images": sorted_list,
            "goals": [result]
        }

        output.append(result)

    return output, event_list

test,aa = gene_goal(file_list1)
with open("single3_goal.json", "w", encoding="utf-8") as f:
    json.dump(aa, f, ensure_ascii=False, indent=2)