import re
import uuid
from typing import Optional, Dict, Any, List, Tuple

# Priority mappings
PRIORITY_MAP = {
    "🔺": 1,
    "⏫": 2,
    "🔼": 3,
    "🔽": 4,
    "⏬": 5,
}

# Regex for task checkbox: matches "- [ ]", "* [ ]", "- [x]", "- [-]", etc. with optional leading whitespace
TASK_LINE_REGEX = re.compile(r"^(\s*[-*]\s*\[([ xX\-])\]\s*)(.+)$")

# Regex for ticktick ID: %%[ticktick_id:: ID]%%, %%ticktick_id: ID%%, [ticktick_id:: ID]
TICKTICK_ID_REGEX = re.compile(
    r"(?:%%\[?ticktick_id::?\s*([a-zA-Z0-9_\-]+)\]?%%|\[ticktick_id::\s*([a-zA-Z0-9_\-]+)\]|%%\s*ticktick_id\s+([a-zA-Z0-9_\-]+)\s*%%)"
)

# Regex for aoc ID: %% aoc_id ID %%, %%[aoc_id:: ID]%%, %%aoc_id: ID%%
AOC_ID_REGEX = re.compile(
    r"(?:%%\s*aoc_id\s+([a-zA-Z0-9_\-]+)\s*%%|%%\[?aoc_id::?\s*([a-zA-Z0-9_\-]+)\]?%%|\[aoc_id::\s*([a-zA-Z0-9_\-]+)\])"
)

# Regex for dates (Obsidian Tasks format)
DATE_COMPLETED_REGEX = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")
DATE_DROPPED_REGEX = re.compile(r"❌\s*(\d{4}-\d{2}-\d{2})")
DATE_DUE_REGEX = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
DATE_SCHEDULED_REGEX = re.compile(r"⏳\s*(\d{4}-\d{2}-\d{2})")
DATE_CREATED_REGEX = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")
DATE_START_REGEX = re.compile(r"🛫\s*(\d{4}-\d{2}-\d{2})")

# Regex for tags: #tag or #tag/subtag, excluding #ticktick
TAG_REGEX = re.compile(r"(?:^|\s)#([a-zA-Z0-9_\-\/]+)")

# Trailing metadata links (e.g. [link](https://ticktick.com/...))
TRAILING_LINK_REGEX = re.compile(r"\[link\]\([^)]+\)", re.IGNORECASE)

# Markdown link format: [Text](URL)
MD_LINK_REGEX = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Comments %% ... %%
COMMENT_REGEX = re.compile(r"%%.*?%%", re.DOTALL)


def is_task_line(line: str) -> bool:
    """Checks if a markdown line is a task checkbox."""
    return bool(TASK_LINE_REGEX.match(line))


def extract_id(line: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts the task ID from the line if present.
    Returns (id_value, id_type) where id_type is 'ticktick', 'aoc', or None.
    """
    m_tt = TICKTICK_ID_REGEX.search(line)
    if m_tt:
        id_val = m_tt.group(1) or m_tt.group(2) or m_tt.group(3)
        return id_val.strip(), "ticktick"

    m_aoc = AOC_ID_REGEX.search(line)
    if m_aoc:
        id_val = m_aoc.group(1) or m_aoc.group(2) or m_aoc.group(3)
        return id_val.strip(), "aoc"

    return None, None


def generate_aoc_id() -> str:
    """Generates a unique ID for vault tasks."""
    return uuid.uuid4().hex[:12]


def append_aoc_id(line: str, new_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Appends '%% aoc_id <uuid> %%' to a task line if no ID is present.
    Returns (updated_line, assigned_id).
    """
    existing_id, id_type = extract_id(line)
    if existing_id:
        return line, existing_id

    assigned_id = new_id or generate_aoc_id()
    # Ensure line doesn't end with extra trailing spaces before adding comment
    stripped_line = line.rstrip("\r\n")
    updated_line = f"{stripped_line} %% aoc_id {assigned_id} %%\n"
    return updated_line, assigned_id


def extract_tags(content: str) -> List[str]:
    """
    Extracts all hashtags from the line, ignoring #ticktick.
    """
    raw_tags = TAG_REGEX.findall(content)
    cleaned_tags = []
    for tag in raw_tags:
        clean = tag.strip().lower()
        if clean == "ticktick" or clean.startswith("ticktick/"):
            continue
        cleaned_tags.append(tag.strip())
    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in cleaned_tags:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def extract_priority(content: str) -> Tuple[Optional[str], int]:
    """
    Extracts priority emoji and corresponding rank.
    Returns (priority_emoji, priority_rank).
    """
    for emoji, rank in PRIORITY_MAP.items():
        if emoji in content:
            return emoji, rank
    return None, 99


def extract_dates(content: str) -> Dict[str, Optional[str]]:
    """
    Extracts date metadata from task content.
    """
    completed = DATE_COMPLETED_REGEX.search(content)
    dropped = DATE_DROPPED_REGEX.search(content)
    due = DATE_DUE_REGEX.search(content)
    scheduled = DATE_SCHEDULED_REGEX.search(content)
    created = DATE_CREATED_REGEX.search(content)

    return {
        "completed_date": completed.group(1) if completed else None,
        "dropped_date": dropped.group(1) if dropped else None,
        "due_date": due.group(1) if due else None,
        "scheduled_date": scheduled.group(1) if scheduled else None,
        "created_date": created.group(1) if created else None,
    }


def clean_title(content: str) -> str:
    """
    Cleans the task title by:
    - Removing [link](...) trailing links
    - Unwrapping leading markdown link [Title](url) -> Title if the entire early part is a link
    - Removing #tags
    - Removing Obsidian Tasks emoji metadata (✅, ❌, 📅, ⏳, ➕, 🛫, 🔁, 🔒, 🕸️, priorities)
    - Removing comments %%...%%
    - Cleaning up extra whitespace
    """
    text = content

    # 1. Remove comments %% ... %%
    text = COMMENT_REGEX.sub("", text)

    # 2. Remove trailing [link](https://ticktick.com/...)
    text = TRAILING_LINK_REGEX.sub("", text)

    # 3. Remove date markers and their date strings
    text = re.sub(r"[✅❌📅⏳➕🛫]\s*\d{4}-\d{2}-\d{2}", "", text)

    # 4. Remove other Obsidian task metadata emojis and their arguments
    text = re.sub(r"🔁\s*every\s+[a-zA-Z0-9_\s]+", "", text)
    text = re.sub(r"[🔒🕸️🔁]\s*(?:\[\[.*?\]\]|[^\s]+)*", "", text)

    # 5. Remove priority emojis
    for p_emoji in PRIORITY_MAP.keys():
        text = text.replace(p_emoji, "")

    # 6. Remove tags
    text = TAG_REGEX.sub("", text)

    # 7. Unwrap markdown links: if starts with [Doc Title](http...), convert to Doc Title
    # Handles nested brackets like [Project Title [go/proj] - Docs](https://...)
    nested_link_pattern = re.compile(r"\[((?:\[[^\]]*\]|[^\[\]])+)\]\((?:https?://|www\.)[^\s\)]+\)")
    text = nested_link_pattern.sub(r"\1", text)
    # Also handle standard links
    text = MD_LINK_REGEX.sub(r"\1", text)

    # 8. Clean up extra punctuation/spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_task_line(
    line: str,
    line_number: int,
    source_path: str,
    source_type: str = "vault",
    auto_assign_id: bool = False
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parses a single markdown line into a task dictionary.
    
    If auto_assign_id is True and the task has no ID, assigns an AOC ID and
    returns the updated line in the second tuple element.
    """
    m = TASK_LINE_REGEX.match(line)
    if not m:
        return None, None

    prefix = m.group(1)
    check_char = m.group(2).lower()
    raw_content = m.group(3)

    # Map status
    if check_char == " ":
        status = "todo"
    elif check_char == "x":
        status = "completed"
    elif check_char == "-":
        status = "dropped"
    else:
        status = "todo"

    # ID handling
    task_id, id_type = extract_id(line)
    updated_line = None

    if not task_id:
        if auto_assign_id:
            updated_line, task_id = append_aoc_id(line)
            id_type = "aoc"
        else:
            task_id = generate_aoc_id()
            id_type = "aoc"

    dates = extract_dates(raw_content)
    priority_emoji, priority_rank = extract_priority(raw_content)
    tags = extract_tags(raw_content)
    title = clean_title(raw_content)

    task_data = {
        "id": task_id,
        "title": title,
        "raw_title": raw_content.strip(),
        "status": status,
        "priority": priority_emoji,
        "priority_rank": priority_rank,
        "tags": tags,
        "scheduled_date": dates["scheduled_date"],
        "due_date": dates["due_date"],
        "completed_date": dates["completed_date"],
        "dropped_date": dates["dropped_date"],
        "created_date": dates["created_date"],
        "source": source_path,
        "source_type": source_type,
        "line_number": line_number,
        "raw_line": line.rstrip("\r\n"),
    }

    return task_data, updated_line
