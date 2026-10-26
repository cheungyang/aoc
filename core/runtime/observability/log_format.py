"""Pure helpers for agent logging: tool-call summaries and LLM result metadata."""
import ast
import json
from typing import Any, Optional


def format_tool_extra_str(input_str: Any, tool_name: Optional[str] = None) -> str:
    """
    Format extra string info for tool start logging.
    - For filesystem instructions list: [action "ls" on {directory1}, "read" on {file2}]
    - For agent_call: [agent_id: {agent_id}]
    - For graph_call: [graph_id: {graph_id}]
    - For other tools with action/path/skill_id: [action: create, path: /tmp, skill_id: skill_123]
    """
    if input_str is None:
        return ""

    try:
        input_dict = None
        if isinstance(input_str, dict):
            input_dict = input_str
        elif isinstance(input_str, str):
            try:
                input_dict = json.loads(input_str)
            except Exception:
                try:
                    input_dict = ast.literal_eval(input_str)
                except Exception:
                    input_dict = None

        if not isinstance(input_dict, dict):
            return ""

        instructions = input_dict.get("instructions")
        if isinstance(instructions, str):
            try:
                instructions = json.loads(instructions)
            except Exception:
                try:
                    instructions = ast.literal_eval(instructions)
                except Exception:
                    pass

        if isinstance(instructions, list) and instructions:
            action_items = []
            for inst in instructions:
                if isinstance(inst, dict):
                    act = inst.get("action")
                    p = inst.get("path")
                    if act and p:
                        action_items.append(f'"{act}" on {p}')
                    elif act:
                        action_items.append(f'"{act}"')
                    elif p:
                        action_items.append(f'on {p}')
            if action_items:
                joined = ", ".join(action_items)
                return f" [action {joined}]"

        graph_id = input_dict.get("graph_id") or input_dict.get("graph_name") or input_dict.get("subgraph_name")
        action = input_dict.get("action")
        path = input_dict.get("path")
        skill_id = input_dict.get("skill_id")

        target_agent_id = None
        if tool_name == "agent_call" or (
            "agent_id" in input_dict
            and not action
            and not path
            and not skill_id
            and not graph_id
        ):
            target_agent_id = input_dict.get("agent_id") or input_dict.get("target_agent")

        extra_info = []
        if target_agent_id:
            extra_info.append(f"agent_id: {target_agent_id}")
        if graph_id:
            extra_info.append(f"graph_id: {graph_id}")
        if action:
            extra_info.append(f"action: {action}")
        if path:
            extra_info.append(f"path: {path}")
        if skill_id:
            extra_info.append(f"skill_id: {skill_id}")
        if extra_info:
            return f" [{', '.join(extra_info)}]"
    except Exception:
        pass

    return ""


def extract_model_name(response: Any, gen: Any = None) -> str:
    """
    Returns the model name for an LLM result, or 'unknown'.

    `langchain_google_genai` builds `llm_output` from prompt feedback only, so the name is
    carried on the generation's message (`response_metadata['model_name']`, or `'model'`
    for some providers). `llm_output['model_name']` is kept as a fallback for providers
    that still report it there.
    """
    if gen is None:
        try:
            gen = response.generations[0][0]
        except Exception:
            gen = None

    message = getattr(gen, "message", None) if gen is not None else None
    metadata = getattr(message, "response_metadata", None) if message is not None else None
    generation_info = getattr(gen, "generation_info", None) if gen is not None else None
    for source in (metadata, generation_info):
        if isinstance(source, dict):
            for key in ("model_name", "model"):
                name = source.get(key)
                if isinstance(name, str) and name:
                    return name

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        name = llm_output.get("model_name") or llm_output.get("model")
        if isinstance(name, str) and name:
            return name

    return "unknown"
