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


def ana_pic(image_list_num):
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





multi_evidence_prompt = """Question type constraint:
                    
                    The question you generate must belong to the following question type only:
                    
                    Type: Multi-Evidence Reasoning Questions
                    Description: Require combining multiple pieces of information or sources to perform reasoning. Emphasizes logical inference and cross-content integration.
                    Examples:
                    - Among these laptops, which one is the cheapest?
                    - If I choose this camera and add the previous lens, what is the total cost?
                    - According to the earlier travel-related pages, if the user chooses the selected flight and the cheapest hotel, what would the total cost be in USD?


                    Instructions for this question type:
                    1. The question must require combining two or more pieces of information from the provided frames.
                    2. The answer must be obtained through reasoning over multiple visible facts, rather than from a single directly stated detail.
                    3. The reasoning may involve comparison, aggregation, matching, filtering, or simple arithmetic across frames.
                    4. The question should focus on content that is explicitly visible in the screen records, but the answer must require integrating that content.
                    5. Suitable reasoning patterns include:
                       - comparing prices, ratings, dates, times, or quantities
                       - adding or subtracting visible values
                       - matching an item in one frame with related information in another frame
                       - identifying which option satisfies multiple visible conditions
                       - determining the best or cheapest or earliest or latest choice among multiple visible candidates
                    6. Do not ask questions answerable by a single frame alone without any reasoning.
                    7. Do not ask about hidden user intention, future actions, private preferences, or unsupported assumptions.
                    8. The correct answer must be supported by the referenced frames together, not by one isolated detail.
                    9. The three distractor options should be plausible and should be drawn from other visible content or values appearing in the provided frames.
                    10. Keep the question objective, grounded in the screen content, and dependent on multi-step or multi-source reasoning."""
           
           

                    
                             
                    
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
                      "question": "According to the earlier travel-related pages, if the user chooses the selected flight and the cheapest hotel, what would the total cost be in USD?\nA. 599\nB. c656\nC. 677\nD. 654.34",
                      "answer": "D",
                      "reference": ["13737182162585227244_4",
                                    "3167503509906862684_7",
                                    "3167503509906862684_8",
                                    "15664849218756806894_5"
                                    "15664849218756806894_16"],
                      "reasoning": "The selected flight price is $599. The cheapest visible hotel is the square hotel GINZA at €57. Using the exchange-rate page, €57 converts to about $55.34, so the total is about $654.34.",
                      "timestamp": "15664849218756806894_17"
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
   
    llm_prompt = gene_llm_prompt_nogoal(q_category)
    output = []
    pic_con_all = []
    
    global count
    for i in image_list:
        pic_con = ana_pic(i)
        pic_con_all.append(pic_con)

    result = call_text_llm(system_prompt=llm_prompt, user_prompt=str(pic_con_all))
    try:
        result_j = json.loads(result)
        result_j["question_id"] = "aitw_" + str(folder) + "_" + str(count)
        count = count + 1
    
        result_j["type"] = [category["multi_evidence_prompt"]]
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
        


goal_path = "goal/install_goal.json"
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
    if 700 <= num < 900:
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
    test, returned_image_list = gene_summary_qa(related, multi_evidence_prompt, "general")

    key = f"aitw_{idx}"
    data[key] = {}

    unique_ids = list(set(item for item in returned_image_list))
    data[key]["event_list"] = unique_ids
    data[key]["qa_list"] = []
    data[key]["qa_list"].append(test)



with open("mulevidence_303.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)





