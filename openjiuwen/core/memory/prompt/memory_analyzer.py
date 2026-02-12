# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
MEMORY_ANALYZER_PROMPT = """
# 任务描述
你是一个记忆分析引擎，你的任务是分析当前消息`current_messages`，结合历史消息`historical_messages`，对输入消息进行多维度分析。

## 处理步骤
请严格按照以下步骤顺序执行：

### 步骤1：记忆分类
请将用户提供的消息进行分类，取值范围MEMORY_CATEGORIES_SCOPE，填充到输出模板`categories`字段，结果可能属于多个类别；如果不属于任何类别，则结果为空`[]`。
USER_PROFILE_PROMPT
VARIABLES_DESCRIPTION_TEMPLATE
SUMMARY_TEMPLATE
## 输出格式
1. 输入输出的语言类别应保持一致。如果输入的`current_messages`和`historical_messages`是英文，则输出的记忆内容也应该是英文。
2. 只要消息中包含任何可能对未来对话有帮助的信息，就应当进行分类，避免遗漏需要记忆的信息。
3. 对话中可能存在多个参与用户，需要综合所有参与者的消息进行判断。
4. 最终的输出必须是**纯净的，可直接解析的JSON对象**，不要有任何额外的解释性文字。
5. 保留Markdown格式```json``` 代码块标记。
6. 根据你的分析，填充以下JSON结构，不要修改JSON结构，确保所有字段有值(即使为空也用空字符串或空列表填充)。
```json
{
  "categories":[]
  VARIABLES_OUTPUT_TEMPLATE
  SUMMARY_OUTPUT_TEMPLATE
}
```
"""

USER_PROFILE_CATEGORY = """INDEX. `user_profile`: 指与用户相关的具体信息，包括但不限于以下方面:
 - 姓名、性别、年龄、职业、学历、居住地等个人信息
 - 兴趣爱好与生活习惯（如运动爱好、饮食习惯等）
 - 资产信息与财务状况（如收入、房产、车辆、投资、负债等）
 - 社交关系与沟通方式（如朋友、同事、社交习惯等）
 - 不属于以上类别但对用户有价值的其他信息"""

VARIABLES_DESCRIPTION_TEMPLATE_PROMPT = """
### 步骤2：变量提取
分析对话内容，根据预定义的变量名称和变量描述，完成变量值提取，填充到输出模板`variables`字段。
注意：先分析下面`variables`字段中需要提取和填充的变量内容，`variables`字段中没有出现的变量不需要提取。
变量定义如下：
VARIABLES_DEFINE_TEMPLATE
"""

SUMMARY_TEMPLATE_PROMPT = """
### 步骤{step_num}: 摘要提取
分析当前消息，对消息内容进行摘要提炼，填充到输出模板`summary`字段
注意事项:
- 禁止提取任何用户的隐私信息，包括但不限于身份证号、银行卡号、密码、邮箱地址等，涉及用户隐私可以用模糊描述（如“用户提供了银行卡号和密码”）。
- 基于当前消息进行摘要提炼，完整保留除隐私信息外的所有关键信息。
- 不要摘要任何历史消息，只需对当前消息内容进行摘要提取。
- 记录非隐私信息需要使用具体数值(例如“用户手机号为xxx”), 而非模糊描述(如"用户提供了联系方式")。
- 若消息中存在多个参与用户，需要指明具体用户（例如“用户A手机号为xxx”）。
- 语言简洁明确, 无需任何额外说明或解释, 不需要对摘要内容进行翻译。
- 消息摘要不超过{max_message_token}个词
"""