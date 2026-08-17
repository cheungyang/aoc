import os
import shutil
import fnmatch
from langchain_core.tools import tool
from core.loaders.agents_loader import AgentsLoader
from core.util import format_tool_response

@tool
def filesystem(agent_id: str, instructions: list[dict]) -> str:
    """
    Perform file operations (read, write, overwrite, append, replace_block, ls, move, delete, rmdir, find, grep) with scoped permissions.
    Supports executing multiple actions in a single call.
    
    Supported Actions:
    - 'read': Reads the content of a file. Supports optional 'start_line' and 'end_line' (1-indexed) which prepends line numbers.
        Requires: 'path'. Optional: 'start_line', 'end_line'.
    - 'read_image': Reads an image file and returns its content as a base64-encoded string (for multimodal comprehension).
        Requires: 'path'.
    - 'write': Writes content to a NEW file. Fails if the file already exists.
        Requires: 'path', 'content'.
    - 'overwrite': Overwrites an existing file or creates a new one with the provided content.
        Requires: 'path', 'content'.
    - 'append': Appends content strictly to the end of the file.
        Requires: 'path', 'content'.
    - 'replace_block': Replaces a specific block of text in a file with new content. Normalizes whitespace when matching.
        Requires: 'path', 'old_block', 'new_block'.
    - 'ls': Lists the immediate contents of a specified directory.
        Requires: 'path'.
    - 'move': Renames a file or moves it to a new directory.
        Requires: 'path' (source), 'destination'.
    - 'delete': Deletes a single file. Cannot delete directories.
        Requires: 'path'.
    - 'rmdir': Removes an empty directory. Fails if directory is not empty.
        Requires: 'path'.
    - 'find': Recursively searches for filenames matching a pattern within a directory. Fails if more than 50 results.
        Requires: 'path' (directory to search). Optional: 'pattern' or 'search_string'.
    - 'grep': Recursively searches for text strings inside files within a directory. Fails if more than 50 results.
        Requires: 'path' (directory or file to search), 'search_string' (or 'query'/'content').

    Args:
        agent_id: The ID of the agent running this tool.
        instructions: A list of dictionaries, where each dictionary represents an action to perform.
    """
    if not agent_id:
        return format_tool_response("filesystem", payload="", errors="Error: agent_id is required to verify permissions.")

    from core.loaders.tools_loader import ToolsLoader
    tools_loader = ToolsLoader()

    if len(instructions) > 10:
        return format_tool_response("filesystem", payload="", errors="Error: Too many instructions requested (limit is 10).")

    payload_elements = []
    error_elements = []

    for inst in instructions:
        action = inst.get("action")
        path = inst.get("path")
        
        if action is None or path is None:
            error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Both \'action\' and \'path\' are required in each instruction.</instruction_error>')
            continue

        # Path Permission check for primary path
        if not tools_loader.check_permission(agent_id, "filesystem", action, path):
            error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Agent {agent_id} does not have permission to perform \'{action}\' on path {path}</instruction_error>')
            continue

        # For move action, also check destination permission
        if action == "move":
            destination = inst.get("destination")
            if not destination:
                error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: \'destination\' is required for \'move\' action.</instruction_error>')
                continue
            if not tools_loader.check_permission(agent_id, "filesystem", action, destination):
                error_elements.append(f'<instruction_error action="{action}" path="{path}">Error: Agent {agent_id} does not have permission to perform \'{action}\' on destination {destination}</instruction_error>')
                continue

        p, e = _execute_single_action(inst)
        
        if e and e != "None":
            error_elements.append(f'<instruction_error action="{action}" path="{path}">{e}</instruction_error>')
        else:
            payload_elements.append(f'<instruction_result action="{action}" path="{path}">{p}</instruction_result>')

    full_payload = "\n".join(payload_elements)
    full_errors = "\n".join(error_elements) if error_elements else "None"
    
    return format_tool_response("filesystem", payload=full_payload, errors=full_errors)


def _execute_single_action(inst: dict) -> tuple[str, str]:
    action = inst.get("action")
    path = inst.get("path")

    try:
        if action == "read":
            return _read(path, inst.get("start_line"), inst.get("end_line"))
        elif action == "read_image":
            return _read_image(path)
        elif action == "write":
            return _write(path, inst.get("content", ""))
        elif action == "overwrite":
            return _overwrite(path, inst.get("content", ""))
        elif action == "append":
            return _append(path, inst.get("content", ""))
        elif action == "replace_block":
            return _replace_block(path, inst.get("old_block"), inst.get("new_block"))
        elif action == "ls":
            return _ls(path)
        elif action == "move":
            return _move(path, inst.get("destination"))
        elif action == "delete":
            return _delete(path)
        elif action == "rmdir":
            return _rmdir(path)
        elif action == "find":
            pattern = inst.get("pattern") or inst.get("search_string") or "*"
            return _find(path, pattern)
        elif action == "grep":
            search_string = inst.get("search_string") or inst.get("query") or inst.get("content")
            return _grep(path, search_string)
        else:
            return "", f"Error: Unknown action '{action}'"
    except Exception as e:
        return "", f"Error performing filesystem action: {e}"


def _read(path: str, start_line: int | str | None = None, end_line: int | str | None = None) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: File not found at {path}"
    if not os.path.isfile(path):
        return "", f"Error: Path {path} is not a file."

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        file_content = f.read()

    if start_line is not None or end_line is not None:
        lines = file_content.splitlines()
        total_lines = len(lines)

        s = int(start_line) if start_line is not None else 1
        e = int(end_line) if end_line is not None else total_lines

        if s < 1:
            s = 1
        if e > total_lines:
            e = total_lines

        if s > total_lines or s > e:
            return "", f"Error: Invalid line range {s}-{e} for file with {total_lines} lines."

        sliced_lines = lines[s - 1 : e]
        formatted = [f"{s + idx}: {line}" for idx, line in enumerate(sliced_lines)]
        return "\n".join(formatted), "None"
    else:
        return file_content, "None"


def _read_image(path: str) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: File not found at {path}"
    if not os.path.isfile(path):
        return "", f"Error: Path {path} is not a file."

    from PIL import Image
    from io import BytesIO
    import base64

    try:
        with Image.open(path) as img:
            # Convert to RGB if necessary (JPEG doesn't support transparency)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Resize image to a max of 1024x1024 maintaining aspect ratio
            img.thumbnail((1024, 1024))

            output_buffer = BytesIO()
            # Compress using JPEG format with quality=60
            img.save(output_buffer, format="JPEG", quality=60)

            encoded_string = base64.b64encode(output_buffer.getvalue()).decode("utf-8")
            return encoded_string, "None"
    except Exception as e:
        return "", f"Error reading or compressing image: {e}"


def _write(path: str, content: str) -> tuple[str, str]:
    if os.path.exists(path):
        return "", f"Error: File already exists at {path}. Use 'overwrite' if intentional."
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully wrote to {path}", "None"


def _overwrite(path: str, content: str) -> tuple[str, str]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully overwrote {path}", "None"


def _append(path: str, content: str) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: File not found at {path} to append."
    if not os.path.isfile(path):
        return "", f"Error: Path {path} is not a file."
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully appended to {path}", "None"


def _replace_block(path: str, old_block: str | None, new_block: str | None) -> tuple[str, str]:
    if old_block is None or new_block is None:
        return "", "Error: Both 'old_block' and 'new_block' are required for 'replace_block' action."
    if not os.path.exists(path):
        return "", f"Error: File not found at {path}"
    if not os.path.isfile(path):
        return "", f"Error: Path {path} is not a file."

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        file_content = f.read()

    # 1. Exact match
    exact_count = file_content.count(old_block)
    if exact_count == 1:
        updated_content = file_content.replace(old_block, new_block, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated_content)
        return f"Successfully replaced block in {path}", "None"
    elif exact_count > 1:
        return "", f"Error: old_block is ambiguous (matched {exact_count} times). Provide more surrounding context."

    # 2. Resilient whitespace/newline-normalized matching
    file_lines = file_content.splitlines(keepends=True)
    norm_file_lines = [l.rstrip() for l in file_content.splitlines()]
    norm_old_lines = [l.rstrip() for l in old_block.splitlines()]

    while norm_old_lines and norm_old_lines[-1] == "":
        norm_old_lines.pop()
    while norm_old_lines and norm_old_lines[0] == "":
        norm_old_lines.pop(0)

    if not norm_old_lines:
        return "", "Error: old_block cannot be empty."

    matches = []
    block_len = len(norm_old_lines)
    for i in range(len(norm_file_lines) - block_len + 1):
        if norm_file_lines[i : i + block_len] == norm_old_lines:
            matches.append(i)

    if len(matches) == 1:
        match_idx = matches[0]
        prefix = "".join(file_lines[:match_idx])
        suffix = "".join(file_lines[match_idx + block_len:])

        if suffix and not new_block.endswith("\n"):
            new_block_formatted = new_block + "\n"
        else:
            new_block_formatted = new_block

        updated_content = prefix + new_block_formatted + suffix
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated_content)
        return f"Successfully replaced block in {path}", "None"
    elif len(matches) > 1:
        return "", f"Error: old_block is ambiguous (matched {len(matches)} times with normalized whitespace). Provide more surrounding context."
    else:
        return "", f"Error: old_block not found in {path}"


def _ls(path: str) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: Path not found at {path}"
    if not os.path.isdir(path):
        return "", f"Error: Path {path} is not a directory"
    items = sorted(os.listdir(path))
    return "\n".join(items) if items else "Directory is empty", "None"


def _move(path: str, destination: str | None) -> tuple[str, str]:
    if not destination:
        return "", "Error: 'destination' is required for 'move' action."
    if not os.path.exists(path):
        return "", f"Error: Path not found at {path}"
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    shutil.move(path, destination)
    return f"Successfully moved {path} to {destination}", "None"


def _delete(path: str) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: File not found at {path}"
    if not os.path.isfile(path):
        return "", f"Error: Path {path} is not a file. 'delete' action only deletes single files."
    os.remove(path)
    return f"Successfully deleted file {path}", "None"


def _rmdir(path: str) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: Path not found at {path}"
    if not os.path.isdir(path):
        return "", f"Error: Path {path} is not a directory"
    try:
        os.rmdir(path)
        return f"Successfully removed directory {path}", "None"
    except OSError as e:
        return "", f"Error: Directory not empty or cannot be removed at {path}: {e}"


def _find(path: str, pattern: str = "*") -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: Path not found at {path}"
    if not os.path.isdir(path):
        return "", f"Error: Path {path} is not a directory"

    has_glob = any(char in pattern for char in ["*", "?", "[", "]"])

    matches = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "venv", ".venv")]
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, path)

            matched = False
            if has_glob:
                matched = fnmatch.fnmatch(file, pattern) or fnmatch.fnmatch(rel_path, pattern)
            else:
                matched = pattern.lower() in file.lower() or pattern.lower() in rel_path.lower()

            if matched:
                matches.append(rel_path)

    total_count = len(matches)
    if total_count > 50:
        return "", f"Error: Your search returned {total_count} results. This is too broad and will exceed your context window. Please refine your search_string or specify a deeper directory path."

    if not matches:
        return f"No files found matching pattern '{pattern}' in {path}", "None"

    return "\n".join(matches), "None"


def _grep(path: str, search_string: str | None) -> tuple[str, str]:
    if not os.path.exists(path):
        return "", f"Error: Path not found at {path}"

    if not search_string:
        return "", "Error: 'search_string' is required for 'grep' action."

    matches = []

    def search_file(file_path: str, base_dir: str):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, start=1):
                    if search_string in line:
                        rel = os.path.relpath(file_path, base_dir) if os.path.isdir(base_dir) else os.path.basename(file_path)
                        matches.append(f"{rel}:{line_no}: {line.rstrip()}")
                        if len(matches) > 50:
                            return
        except Exception:
            pass

    if os.path.isfile(path):
        search_file(path, path)
    elif os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "venv", ".venv")]
            for file in files:
                full_path = os.path.join(root, file)
                search_file(full_path, path)
                if len(matches) > 50:
                    break
            if len(matches) > 50:
                break

    total_count = len(matches)
    if total_count > 50:
        return "", f"Error: Your search returned {total_count} results. This is too broad and will exceed your context window. Please refine your search_string or specify a deeper directory path."

    if not matches:
        return f"No matches found for '{search_string}' in {path}", "None"

    return "\n".join(matches), "None"