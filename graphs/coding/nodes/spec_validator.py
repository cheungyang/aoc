import os
from typing import Dict, Any
from graphs.coding.schemas import CodingState
from graphs.coding.prompts.spec_validator_prompt import build_spec_validator_prompt
from graphs.coding.utils.xml_parsers import parse_spec_validation_xml

async def spec_validator_node(state: CodingState) -> Dict[str, Any]:
    """
    Spec Validator Node (Goldfish 0):
    Performs zero-context validation on the target feature spec against the 4-point checklist:
    1. Explicit target file paths
    2. Exact data schemas/types
    3. Testable Given-When-Then Acceptance Criteria
    4. Explicit verification command
    """
    if state.get("error_message") and not state.get("current_task"):
        return {}

    current_task = state.get("current_task") or {}
    target_spec_path = state.get("spec_path") or current_task.get("spec_path", "")

    if not target_spec_path or not os.path.exists(target_spec_path):
        err = f"Spec file not found at {target_spec_path or 'unspecified path'}"
        return {
            "spec_validation_passed": False,
            "spec_validation_feedback": err,
            "error_message": err
        }

    try:
        with open(target_spec_path, "r", encoding="utf-8") as f:
            spec_content = f.read()
    except Exception as e:
        err = f"Failed to read spec file at {target_spec_path}: {e}"
        return {
            "spec_validation_passed": False,
            "spec_validation_feedback": err,
            "error_message": err
        }

    # Build stateless prompt
    prompt = build_spec_validator_prompt(spec_content)
    channel = state.get("channel") or "coding-pipeline"

    try:
        from tools.agent_call import agent_call
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": channel
        })
        parsed = parse_spec_validation_xml(str(tool_res))
    except Exception as e:
        print(f"spec_validator_node: agent_call error: {e}")
        parsed = {
            "verdict": "FAIL",
            "passed": False,
            "unambiguous": False,
            "missing_assumptions": [f"Spec validator agent execution failed: {e}"],
            "summary": f"Spec validation failed to complete due to error: {e}"
        }

    passed = parsed["passed"]
    feedback = f"Verdict: {parsed['verdict']}. Summary: {parsed['summary']}"
    if parsed["missing_assumptions"]:
        feedback += f" Missing: {', '.join(parsed['missing_assumptions'])}"

    return {
        "spec_validation_passed": passed,
        "spec_validation_feedback": feedback,
        "error_message": "" if passed else feedback
    }
