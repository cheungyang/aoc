import os
from typing import Optional
from langchain_core.tools import tool
from graphs.coding.prompts.spec_validator_prompt import build_spec_validator_prompt
from graphs.coding.utils.xml_parsers import parse_spec_validation_xml
from core.util import format_tool_response

@tool
async def spec_validator(
    spec_path: str,
    project_path: Optional[str] = None,
    caller: Optional[str] = None
) -> str:
    """
    Evaluates whether a target Markdown specification is 100% self-contained,
    unambiguous, and ready for autonomous execution before tasks are queued in build_request.json.
    
    Checklist:
    1. Are explicit target file paths defined?
    2. Are exact data schemas/types specified?
    3. Are Acceptance Criteria written in testable Given-When-Then format?
    4. Is there an explicit verification command provided?

    Args:
        spec_path: Path to the Markdown specification file.
        project_path: Root directory of the project.
        caller: The ID of the triggering agent (e.g., 'software-planner').
    """
    if not spec_path:
        return format_tool_response("spec_validator", payload="", errors="Error: 'spec_path' is required.")

    # Resolve spec path relative to project_path
    target_path = spec_path
    if not os.path.isabs(target_path):
        if project_path:
            target_path = os.path.join(project_path, spec_path)
        else:
            target_path = os.path.abspath(spec_path)

    if not os.path.exists(target_path):
        return format_tool_response("spec_validator", payload="", errors=f"Error: Spec file not found at {spec_path} (resolved: {target_path})")

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            spec_content = f.read()
    except Exception as e:
        return format_tool_response("spec_validator", payload="", errors=f"Error reading spec file: {e}")

    prompt = build_spec_validator_prompt(spec_content)

    try:
        from tools.agent_call import agent_call
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": "software-planning"
        })
        payload_str = str(tool_res)
    except Exception as e:
        payload_str = f"""<spec_validation_result>
  <verdict>FAIL</verdict>
  <unambiguous>false</unambiguous>
  <missing_assumptions>
    <item>Spec validation tool execution failed: {e}</item>
  </missing_assumptions>
  <summary>Spec validation failed to complete due to error: {e}</summary>
</spec_validation_result>"""

    return format_tool_response("spec_validator", payload=payload_str, errors="None")
