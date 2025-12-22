# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os

import yaml


def load_yaml_file(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)
            return data
    except yaml.YAMLError as e:
        print(f"YAML解析错误: {e}")
        raise
    except Exception as e:
        print(f"读取文件时出错: {e}")
        raise
