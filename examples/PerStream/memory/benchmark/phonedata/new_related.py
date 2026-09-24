import os
import random
from llm_api import call_text_llm

import json

_data_cache = {}
_json_file_paths = None

def load_json_data(json_paths=None):
    global _data_cache, _json_file_paths

    if json_paths is None:
        possible_paths = [
            "zipdata/aitw_data_train.json",
            "zipdata/aitw_data_val.json",
            "zipdata/aitw_data_test.json",
            "../zipdata/aitw_data_train.json",
        ]
        json_paths = [p for p in possible_paths if os.path.exists(p)]
    elif isinstance(json_paths, str):
        json_paths = [json_paths]

    if not json_paths:
        return {}

    if json_paths == _json_file_paths and _data_cache:
        return _data_cache

    merged_data = {}

    for path in json_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                print(f"[WARNING] {path} 顶层不是 dict，已跳过")
                continue

            for k, v in data.items():
                if k not in merged_data:
                    if isinstance(v, list):
                        merged_data[k] = v[:]   # 拷贝一份
                    else:
                        merged_data[k] = [v]
                else:
                    if isinstance(v, list):
                        merged_data[k].extend(v)
                    else:
                        merged_data[k].append(v)

        except Exception as e:
            print(f"[ERROR] Failed to load JSON from {path}: {e}")

    _data_cache = merged_data
    _json_file_paths = json_paths
    return _data_cache
def load_json_data1(json_path=None):
    """
    加载JSON数据到缓存
    
    Args:
        json_path (str): JSON文件路径。如果为None，使用默认路径
    
    Returns:
        dict: JSON数据
    """
    global _data_cache, _json_file_path
    
    if json_path is None:
        # 尝试找到合适的json文件
        possible_paths = [
            "zipdata/aitw_data_train.json",
            "zipdata/aitw_data_val.json",
            "zipdata/aitw_data_test.json",
            "../zipdata/aitw_data_train.json",
        ]
        for path in possible_paths:
            if os.path.exists(path):
                json_path = path
                break
    
    if json_path is None:
        return {}
    
    # 如果已经加载了这个文件，直接返回缓存
    if json_path == _json_file_path and _data_cache:
        return _data_cache
    
    # 加载JSON文件
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            _data_cache = json.load(f)
            _json_file_path = json_path
        return _data_cache
    except Exception as e:
        print(f"[ERROR] Failed to load JSON from {json_path}: {e}")
        return {}
    

def extract_event_id_from_filename(filename):
    """
    从图片文件名中提取event ID
    
    Args:
        filename (str): 图片文件名
    
    Returns:
        tuple: (event_id, step, folder)
    """
    # 提取基础文件名（去掉路径和扩展名）
    basename = os.path.basename(filename)
    name_without_ext = basename.rsplit('.', 1)[0]
    
    # 确定文件夹
    folder = None
    if 'single/' in filename or filename.startswith('single/'):
        folder = 'single'
        # single文件夹: {event_id}.{step_range}.{description}_{step}
        parts = name_without_ext.split('.')
        if len(parts) >= 3:
            event_id = parts[0] + "." + parts[1]
            # print(event_id)
            # step在最后的_后面
            if '_' in parts[-1]:
                step = int(parts[-1].split('_')[1])
            else:
                step = int(parts[-1])
            return event_id, step, folder
    elif 'general/' in filename or filename.startswith('general/'):
        folder = 'general'
        parts = name_without_ext.rsplit('_', 1)
        if len(parts) >= 2:
            event_id = parts[0]
            step = int(parts[1])
            return event_id, step, folder
    elif 'webshopping/' in filename or filename.startswith('webshopping/'):
        folder = 'webshopping'
        parts = name_without_ext.rsplit('_', 1)
        if len(parts) >= 2:
            event_id = parts[0]
            step = int(parts[1])
            return event_id, step, folder
    elif 'install/' in filename or filename.startswith('install/'):
        folder = 'install'
        parts = name_without_ext.rsplit('_', 1)
        if len(parts) >= 2:
            event_id = parts[0]
            step = int(parts[1])
            return event_id, step, folder
    elif 'googleapps/' in filename or filename.startswith('googleapps/'):
        folder = 'googleapps'
        parts = name_without_ext.rsplit('_', 1)
        if len(parts) >= 2:
            event_id = parts[0]
            step = int(parts[1])
            return event_id, step, folder
    
    # 无法识别的格式
    return None, None, None


def get_goal_by_filename(image_path, json_path=None):
    """
    根据图片文件名获取goal
    
    Args:
        image_path (str): 图片文件路径
        json_path (str): JSON文件路径（可选）
    
    Returns:
        str: goal描述， 如果找不到则返回None
    """
    # 加载JSON数据
    data = load_json_data(json_path)
    if not data:
        return None
    
    # 从文件名中提取event_id, step和文件夹
    event_id, step, folder = extract_event_id_from_filename(image_path)
    if event_id is None or folder is None:
        return None
    
    # 检查folder是否在数据中
    if folder not in data:
        return None
    
    # 在对应的folder中查找
    episodes = data[folder]
    
    # 遍历episodes，找到ep_id以我们提取的event_id开头的episode
    for episode in episodes:
        if not episode:
            continue
        
        # 检查第一个step的ep_id
        first_step = episode[0]
        if 'ep_id' not in first_step:
            continue
        
        # 检查ep_id是否以我们提取的event_id开头
        episode_ep_id = first_step['ep_id']
        if episode_ep_id.startswith(event_id) or episode_ep_id == event_id:
            # 找到了对应的episode，现在找到对应step的记录
            for step_record in episode:
                if step_record.get('step') == step:
                    return folder, event_id, step, step_record.get('goal'), image_path
            
            # 如果对应step的goal没找到，尝试返回第一个step的goal
            return folder, event_id, step, step_record.get('goal'), image_path
    
    return None


import random
import re
from typing import List, Tuple, Set, Optional

# 数据类型： (folder, event_id, step, goal)
GoalItem = Tuple[str, str, int, str]


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


    if result is None:
        print("call_text_llm returned None")
        return 0.0

    try:
        return parse_similarity_score(result)
    except Exception as e:
        print(f"解析失败: {result!r}, error={e}")
        return 0.0


def collect_related_items1(
    items: List[GoalItem],
    seed_item: Optional[GoalItem] = None,
    random_seed: Optional[int] = None,
    include_seed: bool = True,
    threshold: float = 0.8,
):
    if not items:
        raise ValueError("items 不能为空")

    cleaned_items: List[GoalItem] = []
    for folder, event_id, step, goal, image_path in items:
        ng = goal.strip() if goal else ""
        if ng:
            cleaned_items.append((folder, event_id, step, ng, image_path))

    if not cleaned_items:
        raise ValueError("没有有效的 goal")

    if random_seed is not None:
        random.seed(random_seed)

    if seed_item is not None:
        chosen_seed = (
            seed_item[0],
            seed_item[1],
            seed_item[2],
            seed_item[3].strip() if seed_item[3] else "",
            seed_item[4],
        )
        if chosen_seed not in cleaned_items:
            raise ValueError("seed_item 不在 items 中")
    else:
        chosen_seed = random.choice(cleaned_items)

    seed_goal = chosen_seed[3]

    # 如果你要保序，不要用 set，改成 list
    related_items: List[GoalItem] = []
    scored_items: List[Tuple[GoalItem, float]] = []

    goal_score_cache = {}

    for item in cleaned_items:
        current_goal = item[3]

        if item == chosen_seed:
            score = 1.0
        elif current_goal in goal_score_cache:
            score = goal_score_cache[current_goal]
        else:
            try:
                score = llm_similarity(seed_goal, current_goal)
            except Exception as e:
                print(f"[Warning] LLM 判断失败: {seed_goal!r} vs {current_goal!r}, error={e}")
                score = 0.0
            goal_score_cache[current_goal] = score

        scored_items.append((item, score))

        if score >= threshold:
            if item == chosen_seed:
                if include_seed:
                    related_items.append(item)
            else:
                related_items.append(item)

    return chosen_seed, related_items, scored_items


def collect_related_items(
    items: List[GoalItem],
    seed_item: Optional[GoalItem] = None,
    random_seed: Optional[int] = None,
    include_seed: bool = True,
    threshold: float = 0.8,
):
    """
    从 items 中选一个 seed，用 llm_similarity 计算每个 goal 和 seed goal 的相似度。
    保留 score >= threshold 的项。

    去重缓存逻辑：
    - 如果多个 item 的 goal 完全相同，只计算一次相似度
    - 后续相同 goal 直接复用缓存分数

    返回:
        chosen_seed: 选中的种子样本
        related_items: 满足阈值的样本集合
        scored_items: [(item, score), ...] 按分数从高到低排序
    """
    if not items:
        raise ValueError("items 不能为空")

    cleaned_items: List[GoalItem] = []
    for folder, event_id, step, goal, image_path in items:
        ng = goal.strip() if goal else ""
        if ng:
            cleaned_items.append((folder, event_id, step, ng, image_path))

    if not cleaned_items:
        raise ValueError("没有有效的 goal")

    if random_seed is not None:
        random.seed(random_seed)

    if seed_item is not None:
        chosen_seed = (
            seed_item[0],
            seed_item[1],
            seed_item[2],
            seed_item[3].strip() if seed_item[3] else "",
            seed_item[4],
        )
        if chosen_seed not in cleaned_items:
            raise ValueError("seed_item 不在 items 中")
    else:
        chosen_seed = random.choice(cleaned_items)

    seed_goal = chosen_seed[3]

    related_items: Set[GoalItem] = set()
    scored_items: List[Tuple[GoalItem, float]] = []

    # 缓存：key 是 current_goal，value 是 seed_goal 和 current_goal 的相似度
    goal_score_cache = {}

    if include_seed:
        related_items.add(chosen_seed)
        scored_items.append((chosen_seed, 1.0))
        goal_score_cache[seed_goal] = 1.0

    for item in cleaned_items:
        if item == chosen_seed:
            continue

        current_goal = item[3]

        if current_goal in goal_score_cache:
            score = goal_score_cache[current_goal]
        else:
            try:
                score = llm_similarity(seed_goal, current_goal)
                print(score)
                goal_score_cache[current_goal] = score
            except Exception as e:
                score = 0.0
                goal_score_cache[current_goal] = score

        scored_items.append((item, score))

        if score >= threshold:
            related_items.add(item)

    return chosen_seed, related_items, scored_items





def sim_output(file_list):
    info_list = []
    for i in file_list:
        aa = get_goal_by_filename(i)
        info_list.append(aa)
        
    seed, related, scored = collect_related_items1(
        info_list,
        random_seed=42,
        threshold=0.75
    )
    return seed, related, scored