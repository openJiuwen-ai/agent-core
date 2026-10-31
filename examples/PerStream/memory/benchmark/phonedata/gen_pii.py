import os, re
import random
import json
from llm_api import call_text_llm
from vl_api import call_vl_model
import base64
from new_related import sim_output, get_goal_by_filename, extract_event_id_from_filename, collect_related_items

import random
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

user_related_prompt = """Determine whether the current input is personally relevant to the user.

                        Return true only when the input clearly indicates the information is connected to the user’s own plans, needs, preferences, or likely real-world decisions.
                        
                        Return false in all other cases.
                        
                        Strict rules:
                        
                        Return true only if the input strongly suggests the user may personally act on the information, or that it reflects the user’s own situation, intent, or decision-making.
                        
                        Return false for general information, casual browsing, observation, or curiosity.
                        
                        Any news content should be treated as false unless the input clearly shows a direct personal stake or intended action.
                        
                        News about a place, current events, trends, or general local updates are false.
                        
                        Only return true when a personal connection can be clearly inferred from the input itself.
                        
                        If the personal connection is weak, ambiguous, or speculative, return false.
                        
                        Output only true or false. Do not output anything else.
                        """
import requests
API_KEY = "Bearer"
API_URL = ""
os.environ["no_proxy"] = "localhost,127.0.0.1,.huawei.com"
from typing import Dict, Any, Optional
import time
# MODEL_TEXT = "gpt-oss-120b-tools"
MODEL_TEXT = "minimax-m27"
def user_related(system_prompt: str, user_prompt: str, max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:

    
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
            response = requests.post(API_URL, headers=headers, json=payload, verify=False, timeout=600)
            
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


pii_cate_prompt = """You're an information speculative processing system.

                    Your task is to identify which user-related categories are associated with the current input and to generate specific privacy information based on category speculation.
                    
                    Allowed categories:
                    Location
                    Age
                    Gender
                    Name
                    Contact
                    Health
                    Family
                    Occupation
                    Education
                    Finance
                    Husband/BF
                    Wife/GF
                    Race
                    Nationality
                    Social Security Number
                    Driver’s License
                    Bank Account
                    Credit Card
                    Phone Number
                    IP Address
                    Email Address
                    Password Hash
                    Passport Number
                    Tax ID
                    Employee ID
                    Student ID
                    Date

                    

                    

                    Strict rules:
                    1. Analyze and extract the above-mentioned category information related to the input.
                    2. Speculate as many relevant categories as possible.
                    3. If there are many related categories, only the 10 most relevant categories are displayed.

                    Output the extracted results in the following exact JSON array format only:
                    ["category1", "category2", "category3",......]
                                       
                    Examples:
                   
                    Input: "Looking at 2-bedroom hotels in Osaka."
                    Output:
                    ["Name", "Wife", "Bank Account"]                    
                    
                    Input: "Looking for lastest version compute on target.com."
                    Output:
                    ["Credit Card", "IP Address", "Education"]
                    



                    Input: "Search for the current US dollar to British pound exchange rate and then look up where to buy a nice beach cooler online."
                    Output:
                    ["Finance","Nationality","Family","Name","Contact"]
                    

                    """


import ast
def parse_list_from_text(text):
    match = re.search(r"\[[\s\S]*?\]", text)
    if not match:
        raise ValueError(f"No list found in text: {text}")
    
    return ast.literal_eval(match.group(0))


def find_users(my_dict):
    filter_dict = {}
    for key, value in my_dict.items():
        if user_related(system_prompt=user_related_prompt, user_prompt=str(value["goals"])):
            pii_content = user_related(system_prompt=pii_cate_prompt, user_prompt=str(value["goals"]))
            lst = ast.literal_eval(pii_content)
            if len(lst) > 3:
                filter_dict[key] = value
                filter_dict[key]["pii"] = lst
                
                print()
                print(value["goals"])
                print(pii_content)

            if len(filter_dict) > 15:
                return filter_dict
 

detail_prompt = """Given the input categories and image content provided by the user, generate a short, natural-sounding English snippet that includes all requested categories in sentence form.

                    input format:
                    ["category1", "category2", "category3",......]
                    <image content>
                    
                    Important rules:
                    1. All categories must be included in the output.
                    2. Get details from the input image content and speculate as much as possible.
                    3. The output should read like a realistic mini story or personal statement rather than a specific requirement or interaction.
                    4. Keep it around 8–10 sentences.
                    5. Include every requested category naturally in the paragraph.
                    6. Infer the main incident and add as many relevant contextual details as possible, rather than merely listing sensitive information.
                    
                    Example style:
                    "My family and I are planning a summer trip to the United States. We expect to leave on Jun 15th, 2026, and I will travel using passport number ER6565777. "
                    
                    Now generate one short paragraph using the provided categories and image content.
                    """
                    
 
pii_question = """ You are given multiple privacy categories and their corresponding detailed content. Each record is a list in this format:
                    
                    [
                      "<pii_datagory>",
                      "<detail content>"
                    ]

                                        
                    For example:
                    ["Name", "Email Address", "Password Hash"], ["...detail content description..."]

                    
                    Your task is to generate ONE single-answer question based on the input sequence from the user's point of view.
                    
                    Requirements:

                    1. Review all the provided privacy categories and their content
                    2. Select exactly one privacy category that is the most suitable for creating a question.
                    3. Generate one 4-option single-answer question.
                    4. Exactly one option must be correct.
                    5. Output must be valid JSON only, with no markdown fence, no extra explanation.
                    6. The question should sound natural, keep the wording content-centered, not metadata-centered.


                    Output schema:
                    {
                      "question": "A single-choice question that includes the question stem and exactly four options labeled A, B, C, and D.",
                      "answer": "The correct option label, which must be one of: A, B, C, or D.",
                      "reasoning": "Brief explanation based on the frame content",
                      "category": "the selected privacy category from the iuput"
                    }
                    
                    Rules for question writing:
                    - The question must be answerable from the given content.
                    - The four options should be clear and mutually distinct.
                    - Prefer questions about what the user searched, clicked, viewed, selected, or interacted with.
                    - Do not ask vague or subjective questions.
                    - Do not use information outside the provided input.

                                    
                    Example output:
                    {
                      "question": "What is my home IP Address?\nA. 211.99.17.33\nB. 211.99.17.35\nC. 122.99.17.33\nD. 211.99.27.33",
                      "answer": "A",
                      "reasoning": "explain the answer generated from LLMs",
                      "category":"IP Address"
                    }
                    
                    Now generate the JSON from the provided input frames.
                """
                             


related_frame = """You are given a vignette and a sequence of mobile-phone screen records. Each record is a list in this format:
                    
                    [
                      "<session_id>",
                      <frame_index>,
                      "<screen_description>"
                    ]
                    
                    Interpret the first two fields together as a unique frame identifier:
                    "<session_id>_<frame_index>"
                    
                    For example:
                    ["10468332588775264818", 5, "...description..."]
                    should be treated as frame:
                    "10468332588775264818_5"


                    Your task is to:
                        - Compare the input content with all image descriptions.
                        - Identify the single image that is most relevant to the content.
                        - Return only the ID of the most relevant image.
                        
                        Rules:
                        - Focus on semantic relevance, not just keyword overlap.
                        - Choose exactly one image.
                        - If multiple images seem similar, select the one that best matches the main topic, scene, or intent of the input content.
                        - Do not explain your reasoning.
                        - Do not output anything except the image ID.
                    

                    Output schema: "<session_id>_<frame_index>"
             
                    Example output: "10468332588775264818_5"
                    
                """
 
                    
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
                      "question": "What was the passenger's passport number shown on the traveler information page?\nA. XM48392751\nB. XN43892751\nC. XN48392751\nD. XN48397251",
                      "answer": "A",
                      "reasoning": "The traveler information page explicitly lists the passport number as XN48392751.",

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

def gene_pri_qa(image_list,  folder):
    global count
    output = []
    
    
    for i in image_list:    
        pii_item = {}
        goal = goal_data[i]["goals"]
        if user_related(system_prompt=user_related_prompt, user_prompt=str(goal)):
            try:
                pii_content = user_related(system_prompt=pii_cate_prompt, user_prompt=str(goal))

                try:
                    lst = parse_list_from_text(pii_content)
                except Exception as e:
                    print("Failed to parse pii_content:")
                    print(pii_content)
                    print(e)
                    continue

                pii_item["pii"] = lst 
                pic_img = ana_pic(i)    #pic_content
        
                input_data = lst + [pic_img]
                
                vig = user_related(system_prompt=detail_prompt, user_prompt=str(input_data))  
                pii_item["vig"] = vig   
                

                input2 = lst + [vig]
                
                qa = user_related(system_prompt=pii_question, user_prompt=str(input2))
                qa_2 = json.loads(qa)

                pii_item.update(qa_2)
                

                input3 = [vig, pic_img]
                frame_id = user_related(system_prompt=related_frame, user_prompt=str(input3))
                pii_item["privacy timestamp"] = frame_id
                pii_item["reference"] = frame_id
                part1, part2 = frame_id.rsplit("_", 1)
                pii_item["timestamp"] = part1 + "_" + str(int(part2) + 1)
                pii_item["type"] = [category["privacy_sensitive_prompt"]]
                    

        

                pii_item["question_id"] = "aitw_" + str(folder) + "_" + str(count)
                count = count + 1

            
                output.append(pii_item)
            except Exception as e:
                print("Failed on image:", i)
                print("Error:", e)
                continue

    return output, image_list

from pathlib import Path
goal_path = "goal/general_goal.json"
name = Path(goal_path).stem      # general_goal
part = name.split("_")[0]        # general

with open(goal_path, "r", encoding="utf-8") as f:
    goal_data = json.load(f)
    

num = 0
image_list = []

   

for num, i in enumerate(goal_data):
    if 100 <= num < 150:
        image_list.append(i)
# 每个子列表大小
chunk_size = 5

# 切分成多个列表
split_lists = [
    image_list[i:i + chunk_size]
    for i in range(0, len(image_list), chunk_size)
]
 


data = {}


for idx, sub_image_list in enumerate(split_lists, start=1):
    test, returned_image_list = gene_pri_qa(
        sub_image_list,
        part
    )

    key = f"aitw_{idx}"
    data[key] = {}

    unique_ids = list(set(item for item in returned_image_list))
    data[key]["event_list"] = unique_ids
    data[key]["qa_list"] = []
    data[key]["qa_list"].append(test)



with open("pii_302.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

    






