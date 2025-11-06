"""
Prompt management utilities for AI agents.

This module provides utilities to load prompts, examples, and templates
from the prompts directory structure.
"""

import os
from pathlib import Path
from typing import Dict, Optional

# Base directory for all prompts
PROMPTS_DIR = Path(__file__).parent


def load_prompt_file(agent_name: str, filename: str) -> str:
    """
    Load a prompt file for a specific agent.

    Args:
        agent_name: Name of the agent (e.g., 'dockerfile_generator', 'code_analyzer')
        filename: Name of the file to load (e.g., 'system_instruction.txt', 'prompt_template.txt')

    Returns:
        Contents of the file as a string

    Raises:
        FileNotFoundError: If the file doesn't exist
    """
    file_path = PROMPTS_DIR / agent_name / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_example(agent_name: str, example_name: str) -> str:
    """
    Load an example file for a specific agent.

    Args:
        agent_name: Name of the agent
        example_name: Name of the example file (e.g., 'python_uv.dockerfile')

    Returns:
        Contents of the example file

    Raises:
        FileNotFoundError: If the example doesn't exist
    """
    file_path = PROMPTS_DIR / agent_name / "examples" / example_name

    if not file_path.exists():
        raise FileNotFoundError(f"Example file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_all_examples(agent_name: str) -> Dict[str, str]:
    """
    Load all example files for a specific agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Dictionary mapping example names to their contents
    """
    examples_dir = PROMPTS_DIR / agent_name / "examples"

    if not examples_dir.exists():
        return {}

    examples = {}
    for file_path in examples_dir.glob("*"):
        if file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                examples[file_path.name] = f.read()

    return examples


def format_prompt(template: str, **kwargs) -> str:
    """
    Format a prompt template with the provided variables.

    Args:
        template: The prompt template string
        **kwargs: Variables to substitute in the template

    Returns:
        Formatted prompt string
    """
    # Replace None values with empty strings or defaults
    formatted_kwargs = {}
    for key, value in kwargs.items():
        if value is None:
            formatted_kwargs[key] = ""
        elif isinstance(value, bool):
            formatted_kwargs[key] = str(value)
        else:
            formatted_kwargs[key] = str(value)

    # Use a custom formatter that handles missing keys
    class SafeFormatter(dict):
        def __missing__(self, key):
            return f"{{{key}}}"  # Keep placeholder if key missing

    try:
        return template.format_map(SafeFormatter(formatted_kwargs))
    except Exception as e:
        # Fallback: try to format with what we have
        import re

        result = template
        for key, value in formatted_kwargs.items():
            result = re.sub(r"\{" + re.escape(key) + r"\}", str(value), result)
        return result


__all__ = [
    "PROMPTS_DIR",
    "load_prompt_file",
    "load_example",
    "load_all_examples",
    "format_prompt",
]
