import os
import re
from typing import List, Dict, Any, Tuple

def sanitize_traceback(stderr: str, max_lines: int = 60, max_chars: int = 4000) -> str:
    """
    Truncates and cleans test/compiler failure tracebacks to reduce prompt token footprint
    while preserving the critical failing assertions and stack frames.
    """
    if not stderr:
        return ""
    
    lines = stderr.strip().splitlines()
    if len(lines) > max_lines:
        # Keep first 10 lines (command / error type) and last (max_lines - 10) lines (assertion / stack tail)
        head = lines[:10]
        tail = lines[-(max_lines - 10):]
        truncated_lines = head + [f"\n... [Truncated {len(lines) - max_lines} intermediate runtime lines] ...\n"] + tail
        result = "\n".join(truncated_lines)
    else:
        result = "\n".join(lines)

    if len(result) > max_chars:
        result = result[-max_chars:]
        result = f"... [Truncated leading output] ...\n{result}"

    return result


def sanitize_diff(diff: str, max_chars: int = 8000) -> str:
    """
    Sanitizes git diff output to prevent token bloat from generated/lock files.
    """
    if not diff:
        return ""

    if len(diff) > max_chars:
        # Truncate and append warning
        return diff[:max_chars] + f"\n\n... [Diff truncated at {max_chars} chars to fit context window] ..."
    return diff


def check_static_bloated_files(workspace_path: str, modified_files: List[str], max_lines: int = 150) -> List[Dict[str, Any]]:
    """
    Deterministically checks if any modified file exceeds line count limit.
    Returns list of anti-pattern detection dictionaries.
    """
    violations = []
    if not workspace_path or not modified_files:
        return violations

    for rel_path in modified_files:
        full_path = os.path.join(workspace_path, rel_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    line_count = sum(1 for _ in f)
                if line_count > max_lines:
                    violations.append({
                        "rule": "Bloated Files",
                        "file": rel_path,
                        "line_numbers": f"1-{line_count}",
                        "evidence": f"File has {line_count} lines, exceeding the 150-line modularization threshold."
                    })
            except Exception:
                pass
    return violations


def check_static_silent_failures(workspace_path: str, modified_files: List[str]) -> List[Dict[str, Any]]:
    """
    Deterministically checks for obvious swallowed catch/except blocks in modified files.
    """
    violations = []
    if not workspace_path or not modified_files:
        return violations

    # Patterns for empty catch or pass in except
    py_empty_except = re.compile(r"except\s*(?:[A-Za-z0-9_,\s\(\)]*):\s*(?:pass|\.\.\.)\s*$", re.MULTILINE)
    js_empty_catch = re.compile(r"catch\s*\([^\)]*\)\s*\{\s*\}", re.MULTILINE)

    for rel_path in modified_files:
        full_path = os.path.join(workspace_path, rel_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                
                # Check Python
                if rel_path.endswith(".py"):
                    for m in py_empty_except.finditer(content):
                        line_no = content[:m.start()].count("\n") + 1
                        violations.append({
                            "rule": "Silent Failure",
                            "file": rel_path,
                            "line_numbers": str(line_no),
                            "evidence": f"Swallowed exception: `{m.group(0).strip()}` without logging or bubbling."
                        })
                
                # Check JS/TS
                if rel_path.endswith((".js", ".ts", ".jsx", ".tsx")):
                    for m in js_empty_catch.finditer(content):
                        line_no = content[:m.start()].count("\n") + 1
                        violations.append({
                            "rule": "Silent Failure",
                            "file": rel_path,
                            "line_numbers": str(line_no),
                            "evidence": f"Empty catch block `{m.group(0).strip()}` swallows error silently."
                        })
            except Exception:
                pass
    return violations
