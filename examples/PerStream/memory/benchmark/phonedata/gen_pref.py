import os
import random
import json
from llm_api import call_text_llm
from vl_api import call_vl_model
import base64
from new_related import sim_output, get_goal_by_filename, extract_event_id_from_filename, collect_related_items


import re
from typing import List, Tuple, Set, Optional
os.environ["no_proxy"] = "localhost,127.0.0.1,.huawei.com"

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


vl_sys_prompt = """You are an expert at analyzing mobile phone screenshots.

            First, determine what type of page this screenshot shows.
            Then, describe what content is visible on the page.
            
            Output format:
            - Page type:
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
            

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import base64
import time


def process_one_image(img_path, max_retries=3):
    """
    处理单张图片：
    1. 读取图片
    2. base64 编码
    3. 调用 vl model
    4. 返回 [name, desc]
    """

    for attempt in range(max_retries):
        try:
            with open(img_path, "rb") as f:
                enc = base64.b64encode(f.read()).decode("utf-8")

            desc = call_vl_model(
                vl_sys_prompt,
                enc,
                "Describe this picture in detail.",
                max_tokens=1000
            )

            name = Path(img_path).stem
            return [name, desc]

        except Exception as e:
            print(f"Error processing {img_path}, attempt {attempt + 1}: {e}")

            # 简单重试等待，避免接口临时失败
            time.sleep(1 + attempt)

    return None


def ana_pic(image_list_num, max_workers=5):
    """
    并行分析一个样本里的所有图片。

    image_list_num 是 goal_data 里的 key，例如：
    "10039701842972332757"

    max_workers 控制并发数量：
    3 / 5 / 10 都可以
    """

    item = goal_data[image_list_num]
    image_paths = item["images"]

    results = [None] * len(image_paths)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(process_one_image, img_path): idx
            for idx, img_path in enumerate(image_paths)
        }

        for future in as_completed(future_to_index):
            idx = future_to_index[future]

            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"Unexpected error: {e}")
                results[idx] = None

    # 去掉失败的 None，同时保持原图片顺序
    results = [r for r in results if r is not None]

    return results


def ana_pic2(image_list_num):
    results = []


    img = goal_data[image_list_num]
    for i in img["images"]:
        try:
            img = i
            enc = base64.b64encode(open(img, 'rb').read()).decode('utf-8')
            desc = call_vl_model(
                vl_sys_prompt,
                enc,
                "Describe this picture in detail.",
                max_tokens=1000
            )
         
            name = i.split("/")[-1].replace(".png", "")
            # a, b = name.rsplit("_", 1)
            # new_item = [a, b, desc]
            new_item = [name, desc]
            results.append(new_item)
    
        except Exception as e:
            print(f"Error processing {i}: {e}")

    return results





           
           
user_preference_prompt = """Question type constraint:
                    
                    The question you generate must belong to the following question type only:
                    
                    Type: User Preference Questions
                    Description: Require understanding user preferences or interests based on historical behavior. Answers depend on long-term or short-term memory.
                    Examples:
                    - Based on my previous purchases, which camera might suit my preferences?
                    - Based on the recently viewed restaurant-related browsing records, which type of cuisine is this person currently more likely to prefer?
                    - Based on the recently viewed consumer electronics browsing records, which type of platform did this person most often use to check product information?



                    Instructions for this question type:
                    1. The question must require inferring the user's preference, interest, or likely choice from patterns in the provided frames.
                    2. The answer should be based on observed user behavior, such as repeated views, searches, clicks, comparisons, purchases, saves, or other consistent signals in the input.
                    3. The question should rely on multiple frames or a sequence of interactions, rather than a single isolated screen.
                    4. The reasoning should connect the user's past behavior in the frames to the predicted preference or likely favored option.
                    5. Suitable preference signals include:
                       - repeatedly viewed product categories or brands
                       - recurring topics, genres, or themes
                       - frequent searches for similar items
                       - repeated engagement with similar price ranges, styles, or features
                       - previous purchases, selections, or bookmarks if visible
                    6. Do not ask about hidden personal traits or preferences that are not supported by the provided frames.
                    7. Do not rely on stereotypes or unsupported assumptions.
                    8. The correct answer must be the option best supported by the visible behavioral evidence in the referenced frames.
                    9. The three distractor options should be plausible alternatives, preferably drawn from other visible items or categories in the frames, but less consistent with the observed behavior.
                    10. Keep the question grounded in the user's interaction history and focused on preference inference rather than direct factual lookup."""
                    
                             
                    
def gene_llm_prompt_nogoal(category_prompt):
    llm_prompt = ("""You are given a sequence of mobile-phone screen records. Each record is a list in this format:
                    
                    [
                      "<session_id>",
                      <frame_index>,
                      "<screen_description>"
                    ]
                    
                    Interpret the first two fields together as a unique frame identifier:
                    "<session_id>_<frame_index>"
                    
                    For example:
                    ["10468332588775264818", 5,  "...description..."]
                    should be treated as frame:
                    "10468332588775264818_5"
                    
                    Your task is to generate ONE single-answer question based on the input sequence from the user's point of view.
                    
                    Requirements:
                    1. Read all frames and understand the user's activity flow.
                    2. Generate one 4-option single-answer question.
                    3. Exactly one option must be correct.
                    4. The other three options must be distractors, and they must be other contents that actually appeared in the provided frames.
                    5. The "reference" field should contain the frame id or frame ids where the correct answer is supported.
                    6. Since the question is based on a summary of multiple images, the "reference" field must contain multiple frame IDs (from all summarized event)that support the correct answer.
                    7. The "timestamp" should be the time when the user would likely ask or generate this question, usually a few frames after the frame containing the correct answer.
                    8. The "reasoning" should briefly explain why the answer is correct based on the frames.
                    9. Output must be valid JSON only, with no markdown fence, no extra explanation.
                    
                    Output schema:
                    {
                      "question": "A single-choice question that includes the question stem and exactly four options labeled A, B, C, and D.",
                      "answer": "The correct option label, which must be one of: A, B, C, or D.",
                      "reference": ["frame_id_1", "frame_id_2"],
                      "reasoning": "Brief explanation based on the frame content",
                      "timestamp": "frame_id_where_question_is_asked"
                    }
                    
                    Rules for question writing:
                    - The question must be answerable from the given frames.
                    - The four options should be clear and mutually distinct.
                    - The three distractors must also come from other input frames.
                    - Prefer questions about what the user searched, clicked, viewed, selected, or interacted with.
                    - Do not ask vague or subjective questions.
                    - Do not use information outside the provided input.
                    - If multiple frames support the same answer, include all relevant frame ids in "reference".
                    - "timestamp" should usually be one of the later frames after the evidence frame, representing when the question could naturally be asked.
               
                    - The question must be sufficiently specific so that, even when there are other similar times, the correct answer can still be identified from the details in the question.
                    - The question must be sufficiently specific so that, even when there are other similar times, the correct answer can still be identified from the details in the question.
                    - The question must be sufficiently specific so that, even when there are other similar times, the correct answer can still be identified from the details in the question.
                 
                    
                    """ 
                    
                    
                    
                    + category_prompt + 
                                    
                    """Example output:
                    {
                      "question": "Based on the recently viewed consumer electronics browsing records, which type of platform did this person most often use to check product information??\nA. Brand official websites  \nB. eBay \nC. Amazon\nD. Walmart",
                      "answer": "A",
                      "reference": ["5173755545559035499_10",
                                "10278034789537090211_12",
                                "13253484412240428787_10",
                                "3951647491556645323_8",
                                "8994587546062745922_20",
                                "10239494494867233792_4",
                                "9016519521634241050_5",
                                "13529129338929475110_11"],
                      "reasoning": "The browsing records contain repeated visits to official brand websites such as TCL, Apple, Samsung, and LG, which outnumber visits to marketplace platforms.",
                      "timestamp": "15409202391483846053_0"
                    }
                    
                    Now generate the JSON from the provided input frames.
                """)
    return llm_prompt


category = {
    "factual_prompt": "Factual Detail Questions",
    "multi_evidence_prompt": "Multi-Evidence Reasoning Questions",
    "privacy_sensitive_prompt": "Privacy-Sensitive Questions",
    "content_summarization_prompt": "Content Summarization Questions",
    "user_preference_prompt": "User Preference Questions"
}


summary_prompt = """You will be given a description of an image. Your task is to produce a short and accurate summary of that description.
                    
                    Requirements:
                    
                    1. Focus on the main subject, key elements, and overall scene.
                    2. Keep the summary concise and clear.
                    3. Do not repeat the original wording too closely.
                    4. Do not add assumptions or details not explicitly supported by the description.
                    5. Use the same language as the input unless instructed otherwise.
                    6. As brief as possible

                    Summarize the following content clearly and concisely. Return only the summary text, and do not include any title, heading, prefix, or label such as "Summary".

                    """
count = 1

def gene_summary_qa(image_list, q_category, folder):
    global count
   
    llm_prompt = gene_llm_prompt_nogoal(q_category)
    output = []
    pic_con_all = []
    
    for i in image_list:
        # pic_con = ana_pic(i)
        pic_con = ana_pic(i, max_workers=5)
        pic_con_all.append(pic_con)

    result = call_text_llm(system_prompt=llm_prompt, user_prompt=str(pic_con_all))
    try:
        result_j = json.loads(result)
        result_j["question_id"] = "aitw_" + str(folder) + "_" + str(count)
        count = count + 1
    
        result_j["type"] = [category["user_preference_prompt"]]
        output.append(result_j)
    
    except Exception:
        pass
    return output, image_list




def parse_similarity_score(text: str) -> float:
    text = str(text).strip()

    match = re.search(r"([01](?:\.\d+)?)", text)
    if not match:
        raise ValueError(f"未找到合法分数: {text!r}")

    score = float(match.group(1))
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"score 超出范围: {score}")

    return score


def llm_similarity(goal_a: str, goal_b: str) -> float:
    system_prompt = """
        You are an expert judge for semantic similarity of mobile UI task goals.
        
        Your task is to output a similarity score between 0 and 1 for two goals.
        
        Scoring guidelines:
        - 1.0: same or nearly identical user intent
        - 0.8-0.9: highly similar intent, same task with minor wording differences
        - 0.5-0.7: somewhat related, same broad domain but different specific goal
        - 0.1-0.4: weakly related
        - 0.0: unrelated
        
        Output requirements:
        - Output only one number between 0 and 1
        - Do not output words
        - Do not output explanations
        """.strip()

    user_prompt = f"""
        Goal A: {goal_a}
        Goal B: {goal_b}
        
        Similarity score:
        """.strip()

    result = call_text_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    print(result)
    if result is None:
        print("call_text_llm returned None")
        return 0.0

    try:
        return parse_similarity_score(result)
    except Exception as e:
        print(f"解析失败: {result!r}, error={e}")
        return 0.0

def collect_related_items(items, threshold, random_seed):

    random.seed(random_seed)

    sample = random.choice(items)

    seed_goal = goal_data[sample]["goals"]
    related_items = []
    scored_items = []

    for item in items:
        current_goal = goal_data[item]["goals"]

        try:
            score = llm_similarity(seed_goal, current_goal)
        except Exception as e:
            print(f"[Warning] LLM 判断失败: {seed_goal!r} vs {current_goal!r}, error={e}")
            score = 0.0

        scored_items.append((item, score))

        if score >= threshold:
            related_items.append(item)

    scored_items.sort(key=lambda x: x[1], reverse=True)

    return sample, related_items, scored_items
        


goal_path = "goal/general_goal.json"
with open(goal_path, "r", encoding="utf-8") as f:
    goal_data = json.load(f)
    

num = 0
image_list = []

# for i in goal_data:
#     image_list.append(i)
#     num += 1
#     if num > 100:
#         break
    

for num, i in enumerate(goal_data):
    if 500 <= num < 600:
        image_list.append(i)     
        
# 每个子列表大小
chunk_size = 20

# 切分成多个列表
split_lists = [
    image_list[i:i + chunk_size]
    for i in range(0, len(image_list), chunk_size)
]
 


data = {}

for idx, sub_image_list in enumerate(split_lists, start=1):
    seed, related, scored = collect_related_items(sub_image_list, threshold = 0.55, random_seed = 22)
    test, returned_image_list = gene_summary_qa(related, user_preference_prompt, "general")

    key = f"aitw_{idx}"
    data[key] = {}

    unique_ids = list(set(item for item in returned_image_list))
    data[key]["event_list"] = unique_ids
    data[key]["qa_list"] = []
    data[key]["qa_list"].append(test)



with open("preference_30.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)





