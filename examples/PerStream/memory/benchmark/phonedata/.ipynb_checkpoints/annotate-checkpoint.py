"""
Annotation utilities for extracting goal information from JSON files
"""
import os
import json

# 全局缓存，避免重复加载
_data_cache = {}
_json_file_path = None


def load_json_data(json_path=None):
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
            event_id = parts[0]
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
                    return step_record.get('goal')
            
            # 如果对应step的goal没找到，尝试返回第一个step的goal
            return first_step.get('goal')
    
    return None


def clear_cache():
    """清除缓存"""
    global _data_cache, _json_file_path
    _data_cache = {}
    _json_file_path = None


if __name__ == "__main__":
    # 测试代码
    test_file = "zipdata/aitw_images/single/2134607173528546981.150-222.Go to chrome search bar and search for walmart_0.png"
    goal = get_goal_by_filename(test_file)
    print(f"Goal for {test_file}:")
    print(f"  {goal}")