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
        "query to answer the question. You may use the {{tool_name}} tool to "
        "test your query and verify the results. If the query returns an error "
        "or unexpected results, analyze the feedback and try again.\n\n"
        "Important guidelines:\n"
        "- Use the exact table and column names from the provided schema.\n"
        "- Pass the database identifier (shown as 'Database: ...') as the "
        "'database' parameter when calling the tool.\n"
        "- When you are confident in your final SQL query, output it in the "
        "following format: {{answer_format}}"
    ),
)
