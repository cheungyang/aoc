import os
import re
import uuid
import yaml
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# Priority mappings (matching core.tasks)
PRIORITY_MAP = {
    "🔺": 1,
    "⏫": 2,
    "🔼": 3,
    "🔽": 4,
    "⏬": 5,
}

# Reverse map for rank to emoji
RANK_TO_PRIORITY = {v: k for k, v in PRIORITY_MAP.items()}

# Status normalization map
STATUS_MAP = {
    "executing": "executing",
    "active": "executing",
    "in-progress": "executing",
    "in_progress": "executing",
    "pause": "paused",
    "paused": "paused",
    "on-hold": "paused",
    "on_hold": "paused",
    "done": "done",
    "completed": "done",
    "finished": "done",
    "discontinued": "discontinued",
    "cancelled": "discontinued",
    "canceled": "discontinued",
    "dropped": "discontinued",
    "abandoned": "discontinued",
    "considering": "considering",
    "someday": "considering",
    "maybe": "considering",
    "backlog": "considering",
    "planning": "planning",
    "planned": "planning",
}

# Regex patterns
FRONTMATTER_REGEX = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
COMMIT_TAG_REGEX = re.compile(r"^c/(?:([🔺⏫🔼🔽⏬])\s*)?(\d{4})$")
STATUS_TAG_REGEX = re.compile(r"^s/(?:([^\w\s]+)\s*)?(.+)$")
INLINE_TAG_REGEX = re.compile(r"(?:^|\s)#([a-zA-Z0-9_\-\/]+)")
ID_REGEX = re.compile(r"^(?:id|uuid|project_id)\s*:\s*([^\r\n]+)", re.MULTILINE)


def generate_project_id() -> str:
    """Generates a unique 12-character hex ID for projects."""
    return uuid.uuid4().hex[:12]


def extract_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Extracts YAML frontmatter and remaining markdown body.
    Returns (frontmatter_dict, body_text).
    """
    match = FRONTMATTER_REGEX.match(content)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body = content[match.end():]
    try:
        data = yaml.safe_load(yaml_text)
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    return {}, body


def extract_id_from_frontmatter(fm: Dict[str, Any], content: str) -> Optional[str]:
    """
    Extracts project ID from parsed frontmatter dictionary or regex search in content.
    """
    for key in ("id", "uuid", "project_id"):
        val = fm.get(key)
        if val is not None and str(val).strip():
            return str(val).strip().strip("\"'")

    match = FRONTMATTER_REGEX.match(content)
    if match:
        fm_text = match.group(1)
        id_match = ID_REGEX.search(fm_text)
        if id_match:
            return id_match.group(1).strip().strip("\"'")

    return None


def inject_frontmatter_id(content: str, new_id: Optional[str] = None) -> Tuple[str, str]:
    """
    Ensures the markdown content has an 'id: <uuid>' in its frontmatter.
    If frontmatter exists without an ID, inserts 'id: <uuid>' right after the first '---'.
    If no frontmatter exists, creates one with the ID.
    Returns (updated_content, assigned_id).
    """
    match = FRONTMATTER_REGEX.match(content)
    if match:
        fm_text = match.group(1)
        id_match = ID_REGEX.search(fm_text)
        if id_match:
            existing_id = id_match.group(1).strip().strip("\"'")
            return content, existing_id

        assigned_id = new_id or generate_project_id()
        lines = content.splitlines(keepends=True)
        for idx, line in enumerate(lines):
            if line.strip() == "---":
                # Match line ending of original file
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                lines.insert(idx + 1, f"id: {assigned_id}{ending}")
                break
        return "".join(lines), assigned_id
    else:
        assigned_id = new_id or generate_project_id()
        return f"---\nid: {assigned_id}\n---\n{content}", assigned_id


def extract_tags(fm: Dict[str, Any], body: str) -> List[str]:
    """
    Extracts all tags from frontmatter and markdown body, deduplicating while preserving order.
    """
    tags_list = []
    fm_tags = fm.get("tags") or []
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]
    elif not isinstance(fm_tags, list):
        fm_tags = [str(fm_tags)]

    for t in fm_tags:
        if t is not None:
            t_str = str(t).strip()
            if t_str and t_str not in tags_list:
                tags_list.append(t_str)

    # Extract inline hashtags from markdown body
    inline_matches = INLINE_TAG_REGEX.findall(body)
    for tag in inline_matches:
        clean_tag = tag.strip()
        hash_tag = f"#{clean_tag}"
        if hash_tag not in tags_list and clean_tag not in tags_list:
            tags_list.append(hash_tag)

    return tags_list


def extract_commitments(tags: List[str]) -> List[Dict[str, Any]]:
    """
    Extracts commitment years and priorities from tags (e.g., 'c/🔺2025', 'c/⏫2026', 'c/2026').
    Returns a list of dicts: [{'year': int, 'priority': str|None, 'priority_rank': int}].
    """
    commitments = []
    seen_years = set()

    for t in tags:
        clean_t = t.lstrip("#").strip()
        m = COMMIT_TAG_REGEX.match(clean_t)
        if m:
            p_emoji, year_str = m.groups()
            year = int(year_str)
            p_rank = PRIORITY_MAP.get(p_emoji, 99) if p_emoji else 99
            if year not in seen_years:
                seen_years.add(year)
                commitments.append({
                    "year": year,
                    "priority": p_emoji,
                    "priority_rank": p_rank
                })

    commitments.sort(key=lambda x: x["year"])
    return commitments


def resolve_primary_commitment(
    commitments: List[Dict[str, Any]],
    fm: Dict[str, Any],
    target_year: Optional[int] = None
) -> Tuple[Optional[int], Optional[str], int]:
    """
    Resolves the primary (current or latest) commitment year, priority emoji, and rank.
    Returns (commitment_year, priority, priority_rank).
    """
    current_year = target_year if target_year is not None else datetime.now().year

    # Check if commitments list has the current year
    for c in commitments:
        if c["year"] == current_year:
            return c["year"], c["priority"], c["priority_rank"]

    # Otherwise fallback to the latest year if commitments exist
    if commitments:
        latest = commitments[-1]
        return latest["year"], latest["priority"], latest["priority_rank"]

    # Fallback to explicit frontmatter priority if present
    fm_priority = fm.get("priority")
    fm_year = fm.get("commitment_year")
    year_val = int(fm_year) if fm_year and str(fm_year).isdigit() else None

    if fm_priority:
        p_str = str(fm_priority).strip()
        if p_str in PRIORITY_MAP:
            return year_val, p_str, PRIORITY_MAP[p_str]
        try:
            rank_int = int(p_str)
            return year_val, RANK_TO_PRIORITY.get(rank_int), rank_int
        except ValueError:
            pass

    return year_val, None, 99


def extract_status(
    fm: Dict[str, Any],
    tags: List[str],
    has_commitments: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts and normalizes project status from tags or frontmatter.
    Returns (normalized_status, raw_status).
    """
    # 1. Check status tags (e.g. s/✊Executing, s/⏸️Pause, s/🟢Done, s/🛑Discontinued, s/💭Considering, s/🐙Planning)
    for t in tags:
        clean_t = t.lstrip("#").strip()
        m = STATUS_TAG_REGEX.match(clean_t)
        if m:
            raw_st = clean_t
            st_text = m.group(2).strip().lower()
            normalized = STATUS_MAP.get(st_text, st_text)
            return normalized, raw_st

    # 2. Check frontmatter status
    fm_st = fm.get("status")
    if fm_st:
        if isinstance(fm_st, list) and fm_st:
            fm_st = fm_st[0]
        raw_st = str(fm_st).strip()
        clean_st = raw_st.lower()
        normalized = STATUS_MAP.get(clean_st, clean_st)
        return normalized, raw_st

    # 3. If there is a commitment tag (e.g. c/🔺2026), default to 'executing'
    if has_commitments:
        return "executing", "implied_active"

    return None, None


def normalize_date_str(val: Any) -> Optional[str]:
    """Normalizes date values into ISO string format (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)."""
    if val is None:
        return None
    if isinstance(val, (datetime,)):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    s = str(val).strip()
    return s if s else None


def extract_dates(
    fm: Dict[str, Any],
    full_path: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """
    Extracts start date, last reviewed date, and last updated time.
    """
    start_val = fm.get("start") or fm.get("start_date") or fm.get("created") or fm.get("created_date")
    start_date = normalize_date_str(start_val)

    reviewed_val = fm.get("reviewed") or fm.get("last_reviewed") or fm.get("review_date")
    last_reviewed = normalize_date_str(reviewed_val)

    updated_val = fm.get("updated") or fm.get("last_updated") or fm.get("last_updated_time") or fm.get("modified")
    last_updated = normalize_date_str(updated_val)

    # Fallback to filesystem mtime if last_updated is not specified in frontmatter
    if not last_updated and full_path and os.path.exists(full_path):
        try:
            mtime = os.path.getmtime(full_path)
            last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return {
        "start_date": start_date,
        "last_reviewed": last_reviewed,
        "last_updated": last_updated
    }


def extract_project_name(fm: Dict[str, Any], file_path: str) -> str:
    """
    Determines project name from frontmatter title/name or falls back to filename without .md.
    """
    title = fm.get("title") or fm.get("name")
    if title and str(title).strip():
        return str(title).strip()

    base = os.path.basename(file_path)
    name_without_ext, _ = os.path.splitext(base)
    return name_without_ext.strip()


def parse_project_content(
    content: str,
    rel_path: str,
    full_path: Optional[str] = None,
    auto_assign_id: bool = False,
    target_year: Optional[int] = None
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Parses markdown content into a structured project dictionary.
    If auto_assign_id is True and no frontmatter ID exists, injects 'id: <uuid>'
    and returns the updated content as the second tuple element.
    Returns (project_dict, updated_content_or_none).
    """
    fm, body = extract_frontmatter(content)
    existing_id = extract_id_from_frontmatter(fm, content)
    updated_content = None

    if existing_id:
        project_id = existing_id
    elif auto_assign_id:
        updated_content, project_id = inject_frontmatter_id(content)
        # Re-extract frontmatter after injection
        fm, body = extract_frontmatter(updated_content)
    else:
        project_id = generate_project_id()

    name = extract_project_name(fm, rel_path)
    tags = extract_tags(fm, body)
    commitments = extract_commitments(tags)
    commitment_year, priority, priority_rank = resolve_primary_commitment(
        commitments, fm, target_year=target_year
    )
    status, raw_status = extract_status(fm, tags, has_commitments=bool(commitments))
    dates = extract_dates(fm, full_path)

    category = fm.get("category")
    category = str(category).strip() if category else None

    p_type = fm.get("type")
    p_type = str(p_type).strip() if p_type else "project"

    aliases_raw = fm.get("aliases") or fm.get("alias") or []
    if isinstance(aliases_raw, str):
        aliases_raw = [aliases_raw]
    aliases = [str(a).strip() for a in aliases_raw if a and str(a).strip()]

    project_data = {
        "id": project_id,
        "name": name,
        "file_path": rel_path,
        "status": status,
        "raw_status": raw_status,
        "commitment_year": commitment_year,
        "priority": priority,
        "priority_rank": priority_rank,
        "commitments": commitments,
        "start_date": dates["start_date"],
        "last_reviewed": dates["last_reviewed"],
        "last_updated": dates["last_updated"],
        "category": category,
        "type": p_type,
        "aliases": aliases,
        "tags": tags,
        "raw_content": updated_content or content,
    }

    return project_data, updated_content


def parse_project_file(
    full_path: str,
    rel_path: Optional[str] = None,
    pkm_dir: Optional[str] = None,
    auto_assign_id: bool = True,
    target_year: Optional[int] = None
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Reads a project markdown file from disk and parses it.
    Returns (project_dict, updated_content_or_none).
    """
    if rel_path is None:
        if pkm_dir:
            rel_path = os.path.relpath(full_path, pkm_dir)
        else:
            rel_path = os.path.basename(full_path)

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return parse_project_content(
        content=content,
        rel_path=rel_path,
        full_path=full_path,
        auto_assign_id=auto_assign_id,
        target_year=target_year
    )
