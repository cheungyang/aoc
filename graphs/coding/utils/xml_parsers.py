import re
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional

def parse_spec_validation_xml(text: str) -> Dict[str, Any]:
    """
    Parses <spec_validation_result> XML block from Spec Validator LLM.
    
    Expected format:
    <spec_validation_result>
      <verdict>PASS | FAIL</verdict>
      <unambiguous>true | false</unambiguous>
      <missing_assumptions>
        <item>Description of missing interface</item>
      </missing_assumptions>
      <summary>Zero-context evaluation rationale</summary>
    </spec_validation_result>
    """
    res = {
        "verdict": "FAIL",
        "passed": False,
        "unambiguous": False,
        "missing_assumptions": [],
        "summary": ""
    }
    
    if not text:
        return res

    # 1. Regex tag extraction (fast & resilient to surrounding text/markdown)
    verdict_m = re.search(r"<verdict>(.*?)</verdict>", text, re.IGNORECASE | re.DOTALL)
    if verdict_m:
        v = verdict_m.group(1).strip().upper()
        res["verdict"] = "PASS" if "PASS" in v else "FAIL"
        res["passed"] = (res["verdict"] == "PASS")

    unambiguous_m = re.search(r"<unambiguous>(.*?)</unambiguous>", text, re.IGNORECASE | re.DOTALL)
    if unambiguous_m:
        u = unambiguous_m.group(1).strip().lower()
        res["unambiguous"] = (u == "true")

    summary_m = re.search(r"<summary>(.*?)</summary>", text, re.IGNORECASE | re.DOTALL)
    if summary_m:
        res["summary"] = summary_m.group(1).strip()

    # Extract items inside <missing_assumptions>
    missing_block_m = re.search(r"<missing_assumptions>(.*?)</missing_assumptions>", text, re.IGNORECASE | re.DOTALL)
    if missing_block_m:
        block = missing_block_m.group(1)
        items = re.findall(r"<item>(.*?)</item>", block, re.IGNORECASE | re.DOTALL)
        res["missing_assumptions"] = [it.strip() for it in items if it.strip()]

    # If regex failed to find verdict, try fallback parsing
    if not verdict_m:
        if "VERDICT: PASS" in text.upper() or "<verdict>PASS" in text.upper():
            res["verdict"] = "PASS"
            res["passed"] = True
        elif "VERDICT: FAIL" in text.upper() or "<verdict>FAIL" in text.upper():
            res["verdict"] = "FAIL"
            res["passed"] = False

    return res


def parse_worker_handoff_xml(text: str) -> Dict[str, Any]:
    """
    Parses <worker_handoff> XML block from Coder Worker LLM.
    
    Expected format:
    <worker_handoff>
      <status>READY_FOR_TEST</status>
      <modified_files>
        <file>relative/path/to/file</file>
      </modified_files>
      <implementation_summary>Concise description of changes made</implementation_summary>
    </worker_handoff>
    """
    res = {
        "status": "READY_FOR_TEST",
        "modified_files": [],
        "implementation_summary": ""
    }
    
    if not text:
        return res

    status_m = re.search(r"<status>(.*?)</status>", text, re.IGNORECASE | re.DOTALL)
    if status_m:
        res["status"] = status_m.group(1).strip()

    summary_m = re.search(r"<implementation_summary>(.*?)</implementation_summary>", text, re.IGNORECASE | re.DOTALL)
    if summary_m:
        res["implementation_summary"] = summary_m.group(1).strip()

    files_block_m = re.search(r"<modified_files>(.*?)</modified_files>", text, re.IGNORECASE | re.DOTALL)
    if files_block_m:
        block = files_block_m.group(1)
        files = re.findall(r"<file>(.*?)</file>", block, re.IGNORECASE | re.DOTALL)
        res["modified_files"] = [f.strip() for f in files if f.strip()]

    return res


def parse_critic_verdict_xml(text: str) -> Dict[str, Any]:
    """
    Parses <critic_verdict> XML block from Critic QA LLM.
    
    Expected format:
    <critic_verdict>
      <verdict>APPROVE | REJECT</verdict>
      <anti_patterns_detected>
        <pattern>
          <rule>Fake It Trap | Happy Path Bias | Silent Failure | Bloated Files</rule>
          <file>path/to/file</file>
          <line_numbers>12-18</line_numbers>
          <evidence>Hardcoded return 'dummy_id' instead of database call</evidence>
        </pattern>
      </anti_patterns_detected>
      <feedback_for_worker>Specific remediation instructions</feedback_for_worker>
    </critic_verdict>
    """
    res = {
        "verdict": "REJECT",
        "passed": False,
        "anti_patterns_detected": [],
        "feedback_for_worker": ""
    }
    
    if not text:
        return res

    verdict_m = re.search(r"<verdict>(.*?)</verdict>", text, re.IGNORECASE | re.DOTALL)
    if verdict_m:
        v = verdict_m.group(1).strip().upper()
        res["verdict"] = "APPROVE" if "APPROVE" in v else "REJECT"
        res["passed"] = (res["verdict"] == "APPROVE")

    feedback_m = re.search(r"<feedback_for_worker>(.*?)</feedback_for_worker>", text, re.IGNORECASE | re.DOTALL)
    if feedback_m:
        res["feedback_for_worker"] = feedback_m.group(1).strip()

    # Extract pattern blocks
    patterns_m = re.findall(r"<pattern>(.*?)</pattern>", text, re.IGNORECASE | re.DOTALL)
    for p_block in patterns_m:
        rule_m = re.search(r"<rule>(.*?)</rule>", p_block, re.IGNORECASE | re.DOTALL)
        file_m = re.search(r"<file>(.*?)</file>", p_block, re.IGNORECASE | re.DOTALL)
        lines_m = re.search(r"<line_numbers>(.*?)</line_numbers>", p_block, re.IGNORECASE | re.DOTALL)
        ev_m = re.search(r"<evidence>(.*?)</evidence>", p_block, re.IGNORECASE | re.DOTALL)

        pattern_dict = {
            "rule": rule_m.group(1).strip() if rule_m else "",
            "file": file_m.group(1).strip() if file_m else "",
            "line_numbers": lines_m.group(1).strip() if lines_m else "",
            "evidence": ev_m.group(1).strip() if ev_m else ""
        }
        if pattern_dict["rule"] or pattern_dict["evidence"]:
            res["anti_patterns_detected"].append(pattern_dict)

    # Fallback check
    if not verdict_m:
        if "VERDICT: APPROVE" in text.upper() or "<verdict>APPROVE" in text.upper():
            res["verdict"] = "APPROVE"
            res["passed"] = True
        elif "VERDICT: REJECT" in text.upper() or "<verdict>REJECT" in text.upper():
            res["verdict"] = "REJECT"
            res["passed"] = False

    return res
