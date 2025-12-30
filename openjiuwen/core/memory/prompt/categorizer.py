# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
CATEGORIZATION_PROMPT = """
# 任务描述
你是一个记忆分类专家，你的任务是分析当前消息`current_messages`，结合历史消息`historical_messages`，并将其分类到以下一个或多个类别中：
1. `user_profile`: 指与用户相关的具体信息，包括但不限于以下方面:
 - 姓名、性别、年龄、职业、学历、居住地等个人信息
 - 兴趣爱好与生活习惯（如运动爱好、饮食习惯等）
 - 资产信息与财务状况（如收入、房产、车辆、投资、负债等）
 - 社交关系与沟通方式（如朋友、同事、社交习惯等）
 - 不属于以上类别但对用户有价值的其他信息
# 注意事项
**注意**结合历史消息，综合上下文信息做出分析
# 输出格式
* 提取记忆分类填充以下JSON结构，不要有任何额外的解释性文字：
{"categories":[]}
* 一个记忆可能属于多个类别，类别类型取于以下字段：`user_profile`
* 如果不属于任何类别，请返回空列表{"categories":[]}。
* 保留Markdown格式```json``` 代码块标记。
"""
