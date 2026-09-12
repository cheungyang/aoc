"""Trimming the text that goes into a prompt.

Both helpers exist because the failing case is the expensive one: a test suite
that blew up produces the longest traceback, and that is exactly when the prompt
is at risk of crowding out the spec it needs to fix the problem.
"""


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
