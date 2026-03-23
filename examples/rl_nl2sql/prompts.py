# -*- coding: UTF-8 -*-
"""
Prompt templates for the NL2SQL training scenario.
"""

from openjiuwen.core.foundation.prompt import PromptTemplate

NL2SQL_SYSTEM_PROMPT = PromptTemplate(
    name="nl2sql_system",
    content=(
        "You are a {{role}}. You have access to the {{tool_name}} tool that "
        "can execute SQL queries on a SQLite database.\n\n"
        "Given a database schema and a natural language question, write a SQL "
        "query to answer the question. You MUST use the {{tool_name}} tool "
        "to verify your SQL before providing the final answer.\n\n"
        "Important guidelines:\n"
        "- Use the {{tool_name}} tool to execute your SQL and verify it works.\n"
        "- If there is an error, analyze the feedback and try again.\n"
        "- Only after the SQL executes successfully, output the final answer "
        "in the following format: {{answer_format}}"
    ),
)
