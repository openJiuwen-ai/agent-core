#!/usr/bin/env python
# coding: utf-8
"""
OpenRouter Function Call Example
Demonstrates how to use function calling with OpenRouter API
"""

import json
import os

import requests
from openai import OpenAI


# Initialize OpenAI client for OpenRouter
openai_client = OpenAI(
    api_key=os.getenv("API_KEY", "your_api_key_here"),
    base_url=os.getenv("API_BASE", "https://openrouter.ai/api/v1")
)

# Model configuration
MODEL_NAME = os.getenv("MODEL_NAME", "google/gemini-2.0-flash-001")


def search_gutenberg_books(search_terms):
    """Search for books in the Project Gutenberg library"""
    search_query = " ".join(search_terms)
    url = "https://gutendex.com/books"
    response = requests.get(url, params={"search": search_query})

    simplified_results = []
    for book in response.json().get("results", []):
        simplified_results.append({
            "id": book.get("id"),
            "title": book.get("title"),
            "authors": book.get("authors")
        })

    return simplified_results


tools = [
    {
        "type": "function",
        "function": {
            "name": "search_gutenberg_books",
            "description": (
                "Search for books in the Project Gutenberg library "
                "based on specified search terms"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": (
                            "List of search terms to find books in the "
                            "Gutenberg library (e.g. ['dickens', 'great'] "
                            "to search for books by Dickens with 'great' "
                            "in the title)"
                        )
                    }
                },
                "required": ["search_terms"]
            }
        }
    }
]

TOOL_MAPPING = {
    "search_gutenberg_books": search_gutenberg_books
}


def call_llm(msgs):
    """Call the LLM with messages and tools"""
    resp = openai_client.chat.completions.create(
        model=MODEL_NAME,
        tools=tools,
        messages=msgs
    )
    msgs.append(resp.choices[0].message.model_dump())
    return resp


def get_tool_response(response):
    """Execute tool call and return response"""
    tool_call = response.choices[0].message.tool_calls[0]
    tool_name = tool_call.function.name
    tool_args = json.loads(tool_call.function.arguments)

    # Look up the correct tool locally, and call it with the provided arguments
    # Other tools can be added without changing the agentic loop
    tool_result = TOOL_MAPPING[tool_name](**tool_args)

    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps(tool_result),
    }


def run_agent_loop(user_query: str, max_iterations: int = 10):
    """
    Run the agent loop with function calling

    Args:
        user_query: The user's question or request
        max_iterations: Maximum number of iterations to prevent infinite loops

    Returns:
        The final response from the LLM
    """
    messages = [
        {"role": "user", "content": user_query}
    ]

    iteration_count = 0

    while iteration_count < max_iterations:
        iteration_count += 1
        resp = call_llm(messages)

        if resp.choices[0].message.tool_calls is not None:
            messages.append(get_tool_response(resp))
        else:
            break

    if iteration_count >= max_iterations:
        print("Warning: Maximum iterations reached")

    return messages[-1].get('content', 'No response')


def main():
    """Main function demonstrating function calling"""
    print("OpenRouter Function Call Example")
    print("=" * 50)

    # Example query
    query = "Find books by Charles Dickens with 'great' in the title"
    print(f"Query: {query}")
    print("-" * 50)

    result = run_agent_loop(query)
    print(f"Result: {result}")


if __name__ == "__main__":
    main()
