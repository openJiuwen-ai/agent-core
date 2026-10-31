import os
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


           
           
content_summarization_prompt = """Question type constraint:
                    
                    The question you generate must belong to the following question type only:
                    
                    Type: Content Summarization Questions
                    Description: Users want the AI to summarize a video or content segment, extracting key points, steps, or events.
                    Examples:
                    - Based on the key frames of this reminder-setting process, what did the user finally add in the Clock app?
                    - From the recently viewed pages containing information about Japan, which option is the most accurate summary?


                    Instructions for this question type:
                    1. The question must ask for a summary of content, actions, events, steps, or information shown across the provided frames.
                    2. The answer should require condensing multiple details into a concise summary, rather than identifying one isolated fact.
                    3. The question may focus on:
                       - the main topic of the content
                       - the key steps in a process
                       - the main events in a sequence
                       - the important points from an article, video, or tutorial
                       - what happened across a set of related screens
                    4. The question must be grounded in the provided frames only.
                    5. Do not ask about hidden intent, future actions, personal preference, or unsupported interpretation.
                    6. The correct answer must reflect the best summary supported by the referenced frames.
                    7. The three distractor options should be alternative summaries built from partial, incomplete, or incorrect combinations of visible content from the input.
                    8. The question should usually rely on multiple frames or multiple pieces of content, since summarization requires integrating information.
                    9. Keep the question objective, concise, and focused on summarizing visible content rather than inferring beyond it.
                    10. Prefer questions such as summarizing steps, explaining the main idea, or identifying the best overall summary of a content segment.
                    11. the "reference" must cite distinct frames from different events.
                    
                    !!the "reference" must cite distinct frames from different events.!!
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
                      "question": "Based on the video, which statement best summarizes what the user was primarily doing?\nA. what's the news in laos today\nB. current events in laos 2022\nC. laotian times\nD. vientiane times",
                      "answer": "A",
                         "reference": ["11550999498863875080_5",
                                    "11581353966697803764_6",
                                    "11581353966697803764_3",
                                    "1573011270838677844_8"
                                    ],
                      "reasoning": "The screen description for frame 111550999498863875080_5, 11581353966697803764_3, 1573011270838677844_8 explicitly states that the query text in the search field is 'what's the news in laos today'. The other options are search suggestions shown below the search bar.",
                      "timestamp": "1573011270838677844_11"
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
        pic_con = ana_pic(i)
        pic_con_all.append(pic_con)

    result = call_text_llm(system_prompt=llm_prompt, user_prompt=str(pic_con_all))
    try:
        result_j = json.loads(result)
        result_j["question_id"] = "aitw_" + str(folder) + "_" + str(count)
        count = count + 1
    
        result_j["type"] = [category["content_summarization_prompt"]]
        output.append(result_j)
    
    except Exception:
        pass
    return output, image_list


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
    if 100 <= num < 200:
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
    test, returned_image_list = gene_summary_qa(sub_image_list, content_summarization_prompt, "general")

    key = f"aitw_{idx}"
    data[key] = {}

    unique_ids = list(set(item for item in returned_image_list))
    data[key]["event_list"] = unique_ids
    data[key]["qa_list"] = []
    data[key]["qa_list"].append(test)



with open("summary_302.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

    






