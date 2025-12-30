# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
EXTRACT_VARIABLES_PROMPT = """
# 任务描述
假设你是信息提取领域专家。
# 任务定义
分析LLM智能体的对话内容，根据预定义的变量名称和变量描述，完成目标变量值提取。
变量定义如下:
<定义>
{variables}
</定义>
# 注意事项
1. 仅从当前消息`current_messages`中提取变量值，不要直接提取历史消息`historical_messages`中的信息，历史消息仅只作为辅助信息。
2. 当消息中含有时间关系、逻辑关系等，进行合理推理（比如：从"我明年就20岁了"推理出"年龄：19岁"；从"我去年25岁"推理出"年龄：26岁"；从"我快要大学毕业了"推理出"学历：本科"）
# 输出格式
 - 不要使用示例的内容作为输出。
 - 提取变量值并填充以下JSON结构，输出纯净的，可直接解析的JSON对象，不要有任何额外的解释性文字:
{variables_output_format}
 - 若无法提取某个变量的值，则在对应字段中填入"null"。
 - 保留Markdown格式```json``` 代码块标记。
"""
