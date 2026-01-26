import os


def process_input(task_description, task_file_name):
    """
    Enriches task description with file context information.

    Args:
        task_description: The original task description
        task_file_name: Optional file path associated with the task

    Returns:
        Enhanced task description with file handling instructions
    """
    # TODO: Support URL differentiation (YouTube, Wikipedia, general URLs)

    if not task_file_name:
        return task_description

    # Validate file existence
    if not os.path.isfile(task_file_name):
        raise FileNotFoundError(f"Error: File not found {task_file_name}")

    # Map file extensions to descriptive types
    extension_mappings = {
        'image': ['jpg', 'jpeg', 'png', 'gif', 'webp'],
        'text': ['txt'],
        'json': ['json', 'jsonld'],
        'excel': ['xlsx', 'xls'],
        'pdf': ['pdf'],
        'document': ['docx', 'doc'],
        'html': ['html', 'htm'],
        'ppt': ['pptx', 'ppt'],
        'wav': ['wav'],
        'mp3': ['mp3', 'm4a'],
        'zip': ['zip']
    }

    # Extract and normalize file extension
    ext = task_file_name.rsplit('.', 1)[-1].lower()

    # Determine file type category
    file_category = ext  # Default to extension itself
    for category, extensions in extension_mappings.items():
        if ext in extensions:
            file_category = category.capitalize()
            break

    # Append file context and usage instructions
    file_instruction = (
        f"\nNote: A {file_category} file '{task_file_name}' is associated with this task. "
        f"You should use available tools to read its content if necessary through {task_file_name}. "
        f"Additionally, if you need to analyze this file by Linux commands or python codes, "
        f"you should upload it to the sandbox first. Files in the sandbox cannot be accessed by other tools.\n\n"
    )

    return task_description + file_instruction

