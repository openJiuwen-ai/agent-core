import json

from openjiuwen.core.common.utils.dict_utils import extract_leaf_nodes, rebuild_dict_from_paths, \
    rebuild_dict, format_path


def test_extract_leaf():
    # 创建一个复杂的多层字典
    sample_data = {
        "user": {
            "profile": {
                "name": "张三",
                "age": 25,
                "address": {
                    "city": "北京",
                    "street": "朝阳路"
                }
            },
            "settings": {
                "notifications": True,
                "language": "中文"
            }
        },
        "system": {
            "version": "1.0.0",
            "modules": ["auth", "payment", "analytics"],
            "config": {
                "timeout": 30,
                "retry_count": 3
            }
        },
        "status": "active"
    }

    # 提取并打印所有叶子节点
    leaves = print_leaf_nodes(sample_data)

    print(f"\n总共找到 {len(leaves)} 个叶子节点")
    tree = rebuild_dict(leaves)
    print(json.dumps(tree, ensure_ascii=False, indent=2))
    # 另一种输出格式：简洁版本
    print("\n简洁格式:")
    for path, value in leaves:
        path_str = ".".join(str(p).replace('[', '').replace(']', '') for p in path)
        print(f"{path_str} = {value}")


def print_leaf_nodes(data):
    """
    打印所有叶子节点的路径和值
    """
    leaves = extract_leaf_nodes(data)

    print("所有叶子节点:")
    print("-" * 50)
    for path, value in leaves:
        # 将路径转换为字符串形式
        # path_str = " -> ".join(str(p) for p in path)
        path_str = format_path(path)
        print(f"路径: {path_str}")
        print(f"值: {value} (类型: {type(value).__name__})")
        print("-" * 30)

    return leaves


def test_rebuild():
    # 从之前提取的叶子节点重建字典
    sample_leaves = [
        (["user", "profile", "name"], "张三"),
        (["user", "profile", "age"], 25),
        (["user", "profile", "address", "city"], "北京"),
        (["user", "profile", "address", "street"], "朝阳路"),
        (["user", "settings", "notifications"], True),
        (["user", "settings", "language"], "中文"),
        (["system", "version"], "1.0.0"),
        (["system", "config", "timeout"], 30),
        (["system", "config", "retry_count"], 3),
        (["status"], "active")
    ]

    print("原始叶子节点:")
    for path, value in sample_leaves:
        print(f"{' -> '.join(path)} = {value}")

    # 重建字典
    rebuilt_dict = rebuild_dict_from_paths(sample_leaves)

    print("\n重建的字典:")
    import json

    print(json.dumps(rebuilt_dict, ensure_ascii=False, indent=2))

    # 测试包含列表的情况
    list_leaves = [
        (["data", "users", "[0]", "name"], "Alice"),
        (["data", "users", "[0]", "age"], 30),
        (["data", "users", "[1]", "name"], "Bob"),
        (["data", "users", "[1]", "age"], 25),
        (["data", "tags", "[0]"], "python"),
        (["data", "tags", "[1]"], "programming"),
        (["metadata", "count"], 2)
    ]

    print("\n包含列表的叶子节点:")
    for path, value in list_leaves:
        print(f"{' -> '.join(path)} = {value}")

    # 重建包含列表的字典
    rebuilt_with_lists = rebuild_dict(list_leaves)

    print("\n重建的包含列表的字典:")
    print(json.dumps(rebuilt_with_lists, ensure_ascii=False, indent=2))

    # 验证重建是否正确
    print("\n验证重建结果:")
    print(f"用户0姓名: {rebuilt_with_lists['data']['users'][0]['name']}")
    print(f"用户1年龄: {rebuilt_with_lists['data']['users'][1]['age']}")
    print(f"标签1: {rebuilt_with_lists['data']['tags'][1]}")
