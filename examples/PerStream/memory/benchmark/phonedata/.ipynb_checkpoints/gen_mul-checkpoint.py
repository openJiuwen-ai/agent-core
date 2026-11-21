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
                max_tokens=1000
            )
     
            name = i.split("/")[-1].replace(".png", "")
            a, b = name.rsplit("_", 1)
            new_item = [a, b, desc]
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
                      "question": "What search query is shown in the search bar?\nA. what's the news in laos today\nB. current events in laos 2022\nC. laotian times\nD. vientiane times",
                      "answer": "A",
                      "reference": ["10468332588775264818_5"],
                      "reasoning": "The screen description for frame 10468332588775264818_5 explicitly states that the query text in the search field is 'what's the news in laos today'. The other options are search suggestions shown below the search bar.",
                      "timestamp": "10468332588775264818_6"
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

                    Summarize the following content clearly and concisely. Return only the summary text, and do not include any title, heading, prefix, or label such as "Summary".

                    """



def gene_summary_qa(file_list, q_category, folder):
    event_list = extract_event_list_nogoal(file_list)
    llm_prompt = gene_llm_prompt_nogoal(q_category)
    output = []
    count = 1
    batch_keys_list = []

    if not isinstance(event_list, dict):
        raise ValueError("event_list 必须是 dict，格式如 {event_id: event_content}")

    event_keys = list(event_list.keys())


    idx = 0
    total = len(event_keys)

    while idx < total:
        remain = total - idx

        # 剩余不足 3 个时，直接一起处理
        if remain <= 3:
            batch_size = remain
        else:
            batch_size = 3
            print(batch_size)

            # 如果这次取完后剩下不足 3 个，就把剩下的并到当前 batch
            if remain - batch_size < 3:
                batch_size = remain

        batch_keys = event_keys[idx: idx + batch_size]
        batch_events = [event_list[k] for k in batch_keys]
        batch_keys_list.append(batch_keys)

        try:
            pic_con_list = []
            for event in batch_events:
                pic_con = ana_pic(event)
                print(pic_con)
                for i in pic_con:
                    short = call_text_llm(system_prompt=summary_prompt, user_prompt=i[-1])
                    i[-1] = short            
                pic_con_list.append(pic_con)

            print("gene qa")
            
            result = call_text_llm(
                system_prompt=llm_prompt,
                user_prompt=json.dumps(pic_con_list, ensure_ascii=False)
            )

            result_j = json.loads(result)
            if not isinstance(result_j, dict):
                raise ValueError(f"LLM 返回结果不是 dict，而是 {type(result_j)}")

            result_j["question_id"] = f"aitw_{folder}_{count}"
            result_j["type"] = [category["content_summarization_prompt"]]

            output.append(result_j)
            count += 1

        except Exception as e:
            print(f"batch 处理失败, batch_keys={batch_keys}, error={e}")

        idx += batch_size

    return output, event_list, batch_keys_list




folder_path = "zipdata/aitw_images/install"

file_list = sorted([
    f for f in os.listdir(folder_path)
    if os.path.isfile(os.path.join(folder_path, f))
])[500:600]

#file_list1 = [f"zipdata/aitw_images/install/{name}" for name in file_list]
file_list1 = [f"{folder_path}/{name}" for name in file_list]

output, event_list, batch_keys_list = gene_summary_qa(file_list1, content_summarization_prompt, "install")

data = {}
data["aitw_1"] = {}
data["aitw_1"]["event_list"] = [i for i in event_list]
data["aitw_1"]["qa_list"] = []
data["aitw_1"]["qa_list"].append(output) 
data["aitw_1"]["batch"] = batch_keys_list
print(data)


with open("summary_qtmux.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    






