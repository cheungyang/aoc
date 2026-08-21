"""
Unified path resolution, canonicalization, and validation utilities
for content creation workflows.
"""

import os
import glob
import re
from typing import Optional, Dict, Any, List

ASSET_EXTENSION_MAP = {
    "image": "jpg",
    "video_plot": "md",
    "raw_video": "mp4",
    "visual_plate": "mp4",
    "video": "mp4",
    "copy": "md",
    "audio": "wav"
}

def normalize_path(path: Optional[str]) -> str:
    """Normalizes path by stripping extra slashes or relative components while keeping clean format.
    Also ensures the path resolves correctly if it's missing the pkm_dir prefix."""
    if not path:
        return ""
    norm = os.path.normpath(str(path))
    if not os.path.exists(norm):
        try:
            from core.util.config import Config
            pkm_dir = Config().pkm_dir
            if pkm_dir:
                cand = os.path.join(pkm_dir, norm)
                if os.path.exists(cand):
                    local_pkm = "pkm"
                    if os.path.exists(os.path.join(local_pkm, norm)):
                        return os.path.join(local_pkm, norm)
                    return cand
        except Exception:
            pass
    return norm


def resolve_under_project(
    project_path: Optional[str],
    target_path: Optional[str],
    default_subpath: str = ""
) -> str:
    """
    Unified parent-child path resolver. Anchors target_path under project_path:
    - Normalizes paths.
    - Prevents duplicate directory segments (e.g. parent '.../words' + child 'words/horse').
    - If target_path is already absolute or prefixed with project_path, returns it directly.
    - If target_path is empty, falls back to default_subpath under project_path.
    """
    pdir = normalize_path(project_path) if project_path else ""
    target = normalize_path(target_path) if target_path else ""

    if not pdir:
        return target or default_subpath or ""

    if not target:
        return os.path.join(pdir, default_subpath) if default_subpath else pdir

    # If target already starts with pdir or is absolute
    if target.startswith(pdir) or os.path.isabs(target):
        return target

    # Deduplicate overlapping directory segments (e.g. pdir='.../words', target='words/horse')
    pdir_base = os.path.basename(pdir)
    target_parts = target.split(os.sep)
    if target_parts and target_parts[0] == pdir_base:
        rest = os.sep.join(target_parts[1:])
        return os.path.join(pdir, rest) if rest else pdir

    return os.path.join(pdir, target)


def canonicalize_output_path(project_path: Optional[str], output_path: Optional[str], topic: str) -> str:
    """
    Deterministically computes and canonicalizes output_path under project_path,
    preventing path duplications (such as words/words/topic or pkm/pkm/) and ensuring
    all nodes in the graph target the exact same directory on disk.
    """
    topic = str(topic or "").strip().lower()
    pdir = normalize_path(project_path) if project_path else ""
    out_dir = normalize_path(output_path) if output_path else ""

    if not pdir and not out_dir:
        return topic or ""

    if not out_dir and pdir:
        words_subdir = os.path.join(pdir, "words")
        abs_words_subdir = os.path.abspath(words_subdir)
        if (os.path.isdir(words_subdir) or os.path.isdir(abs_words_subdir)) and not pdir.endswith(os.sep + "words") and pdir != "words":
            return os.path.join(words_subdir, topic) if topic else words_subdir
        return os.path.join(pdir, topic) if topic else pdir

    if out_dir and not pdir:
        return out_dir

    # Both pdir and out_dir exist:
    # 1. Check if out_dir already starts with pdir or is absolute matching pdir
    if out_dir.startswith(pdir) or os.path.isabs(out_dir):
        return out_dir

    # 2. Check for duplicate directory segments (e.g. pdir ends with /words and out_dir starts with words/)
    pdir_base = os.path.basename(pdir)
    out_parts = out_dir.split(os.sep)
    if out_parts and out_parts[0] == pdir_base:
        rest = os.sep.join(out_parts[1:])
        return os.path.join(pdir, rest) if rest else pdir

    # 3. If out_dir starts with 'words/' or equals topic:
    if out_parts and out_parts[0] == "words":
        return os.path.join(pdir, out_dir)

    if out_dir == topic:
        words_subdir = os.path.join(pdir, "words")
        abs_words_subdir = os.path.abspath(words_subdir)
        if (os.path.isdir(words_subdir) or os.path.isdir(abs_words_subdir)) and not pdir.endswith(os.sep + "words") and pdir != "words":
            return os.path.join(words_subdir, out_dir)
        return os.path.join(pdir, out_dir)

    # 4. If out_dir is an explicit multi-segment custom path not matching words/:
    if len(out_parts) > 1:
        return out_dir

    # 5. Single segment custom folder:
    words_subdir = os.path.join(pdir, "words")
    abs_words_subdir = os.path.abspath(words_subdir)
    if (os.path.isdir(words_subdir) or os.path.isdir(abs_words_subdir)) and not pdir.endswith(os.sep + "words") and pdir != "words":
        return os.path.join(words_subdir, out_dir)

    return os.path.join(pdir, out_dir)


def infer_paths_from_state(state: dict) -> tuple[str, str]:
    """
    Infers and canonicalizes (project_path, output_path) from state,
    ensuring that if project_path was omitted but asset paths exist in state,
    the directory hierarchy is accurately recovered without mismatch.
    """
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = normalize_path(state.get("project_path", ""))
    output_path = normalize_path(state.get("output_path", ""))

    if not project_path:
        for k in ["image_path", "video_plot_path", "raw_video_path", "remixed_video_path", "copy_path", "source_audio_path"]:
            p = state.get(k)
            if p and isinstance(p, str) and (os.sep in p or "/" in p):
                asset_dir = os.path.dirname(normalize_path(p))
                if asset_dir:
                    if f"{os.sep}words{os.sep}" in asset_dir:
                        project_path = asset_dir.split(f"{os.sep}words{os.sep}")[0]
                    elif asset_dir.endswith(f"{os.sep}words"):
                        project_path = os.path.dirname(asset_dir)
                    elif f"{os.sep}{topic}" in asset_dir:
                        project_path = asset_dir.split(f"{os.sep}{topic}")[0]
                    if not output_path or output_path == topic:
                        output_path = asset_dir
                    break

    canonical_out = canonicalize_output_path(project_path, output_path, topic)
    return project_path, canonical_out


def resolve_project_doc_path(doc_path: Optional[str], project_path: Optional[str], default_filename: str) -> str:
    """Resolves project documentation files (manifest, creator instructions, QC playbook) under project_path."""
    return resolve_under_project(project_path, doc_path, default_filename)


def resolve_asset_path(
    output_path: Optional[str],
    topic: str,
    asset_type: str,
    next_version: bool = False
) -> str:
    """
    Resolves deterministic versioned asset path strictly under output_path.
    If next_version is True, it increments the version counter for a new file (guaranteed >= v2).
    If next_version is False, it returns the highest existing version (or v1 if none).
    """
    topic = str(topic).strip().lower()
    out_dir = normalize_path(output_path)
    ext = ASSET_EXTENSION_MAP.get(asset_type, "dat")
    if not out_dir:
        return f"{topic}_{asset_type}.{ext}" if not next_version else f"{topic}_{asset_type}_v2.{ext}"

    pattern = os.path.join(out_dir, f"{topic}_{asset_type}*.{ext}")
    existing = set(glob.glob(pattern))

    # Also check absolute path if different
    abs_out_dir = os.path.abspath(out_dir)
    if abs_out_dir != out_dir:
        existing.update(glob.glob(os.path.join(abs_out_dir, f"{topic}_{asset_type}*.{ext}")))

    max_v = 0
    for f in existing:
        if os.path.isfile(f) and os.path.getsize(f) > 0:
            base = os.path.basename(f)
            m = re.search(r'_v(\d+)\.', base)
            if m:
                v = int(m.group(1))
                if v > max_v:
                    max_v = v
            elif base == f"{topic}_{asset_type}.{ext}":
                if 1 > max_v:
                    max_v = 1

    if next_version:
        target_v = max(max_v, 1) + 1
    else:
        target_v = max_v if max_v > 0 else 1

    v_str = f"_v{target_v}" if target_v > 1 else ""
    return os.path.join(out_dir, f"{topic}_{asset_type}{v_str}.{ext}")


def extract_aspect_ratio_from_instructions(
    instructions_text: str,
    default_aspect_ratio: str = "16:9"
) -> str:
    """
    Extracts aspect ratio configuration dynamically from creator instructions or manifest markdown.
    Supports formats like:
      - aspect_ratio: 9:16
      - aspect_ratio: "9:16"
      - Aspect Ratio: 9:16 (Vertical)
      - Format: 9:16 (Vertical) / Reels / Shorts / TikTok -> 9:16
      - Format: 16:9 (Landscape) / YouTube -> 16:9
    """
    if not instructions_text:
        return default_aspect_ratio

    # 1. Direct regex for aspect ratio specification
    m = re.search(r'(?:aspect_ratio|aspect|dimensions?)[:=]\s*["\']?([0-9]+:[0-9]+)["\']?', instructions_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # 2. Key phrases in format section
    m_format = re.search(r'(?:format|video format|output format|orientation)[:=]\s*([^\n\r]+)', instructions_text, re.IGNORECASE)
    if m_format:
        val = m_format.group(1).lower()
        if "9:16" in val or "vertical" in val or "portrait" in val or "reels" in val or "shorts" in val or "tiktok" in val:
            return "9:16"
        if "16:9" in val or "horizontal" in val or "landscape" in val or "youtube" in val:
            return "16:9"

    # 3. Explicit ratio mention in markdown text
    if re.search(r'\b9:16\b', instructions_text):
        return "9:16"
    if re.search(r'\b16:9\b', instructions_text):
        return "16:9"

    return default_aspect_ratio


def resolve_task_asset(
    output_path: Optional[str],
    topic: str,
    asset_type: str,
    needs_revision: bool = False
) -> tuple[str, bool]:
    """
    Resolves task asset target path and determines whether generation is required.
    Returns:
        (target_path, should_generate)
    If needs_revision is False and the file exists on disk with non-zero size,
    returns (existing_path, False).
    Otherwise, returns (target_path, True).
    """
    out_dir = normalize_path(output_path)
    existing = resolve_asset_path(out_dir, topic, asset_type, next_version=False)
    if not needs_revision and os.path.isfile(existing) and os.path.getsize(existing) > 0:
        return existing, False
    target = resolve_asset_path(out_dir, topic, asset_type, next_version=needs_revision)
    return target, True


def load_project_context(
    project_path: Optional[str],
    style: str = "3D",
    manifest_path: Optional[str] = "",
    creator_instructions_path: Optional[str] = ""
) -> Dict[str, Any]:
    """
    Loads project guidelines, style-specific character sheet, reference image,
    and aspect ratio from the project directory.
    """
    pdir = normalize_path(project_path)
    style_norm = style.upper() if style.lower() == "3d" else style.capitalize()

    project_guidelines = ""
    manifest = resolve_project_doc_path(manifest_path, pdir, "01_Project_Manifest.md")
    instructions = resolve_project_doc_path(creator_instructions_path, pdir, "02_Creator_Instructions.md")

    for path in [manifest, instructions]:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    project_guidelines += f"\n--- {os.path.basename(path)} ---\n" + f.read()
            except Exception:
                pass

    char_guidelines = ""
    ref_image_path = ""
    char_dir = os.path.join(pdir, "character") if pdir else ""
    if char_dir and os.path.isdir(char_dir):
        for fname in sorted(os.listdir(char_dir)):
            if fname.lower().endswith(".md") and style.lower() in fname.lower():
                sheet_path = os.path.join(char_dir, fname)
                try:
                    with open(sheet_path, "r", encoding="utf-8") as f:
                        raw_text = f.read()

                    m_fm = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw_text, re.DOTALL)
                    if m_fm:
                        fm_text = m_fm.group(1)
                        m_ref = re.search(r'reference_image:\s*["\']?([^"\'\r\n]+)["\']?', fm_text, re.IGNORECASE)
                        if m_ref:
                            cand_ref = os.path.join(char_dir, os.path.basename(m_ref.group(1).strip()))
                            if os.path.isfile(cand_ref):
                                ref_image_path = cand_ref
                        char_guidelines = raw_text[m_fm.end():].strip()
                    else:
                        char_guidelines = raw_text.strip()
                    break
                except Exception:
                    pass

    aspect_ratio = extract_aspect_ratio_from_instructions(project_guidelines + "\n" + char_guidelines)

    return {
        "project_guidelines": project_guidelines,
        "char_guidelines": char_guidelines,
        "ref_image_path": ref_image_path,
        "aspect_ratio": aspect_ratio,
        "style_normalized": style_norm,
        "manifest_path": manifest,
        "creator_instructions_path": instructions
    }


def validate_inter_node_paths(state: dict, node_name: str) -> None:
    """
    Validates inter-node invariant:
    Ensures that output_path and all asset paths in state are consistent,
    non-divergent, and mapped to the same canonical directory structure.
    """
    output_path = state.get("output_path")
    if not output_path:
        return

    norm_output = normalize_path(output_path)

    path_keys = ["image_path", "video_plot_path", "raw_video_path", "remixed_video_path", "copy_path"]
    for k in path_keys:
        p = state.get(k)
        if p and isinstance(p, str):
            norm_p = normalize_path(p)
            abs_norm_p = os.path.abspath(norm_p)
            abs_norm_out = os.path.abspath(norm_output)
            if not (norm_p.startswith(norm_output) or os.path.dirname(abs_norm_p) == abs_norm_out or abs_norm_p.startswith(abs_norm_out)):
                from graphs.content_creation.utils.invariants import AssetInvariantError
                raise AssetInvariantError(
                    f"Path mismatch between nodes at '{node_name}': "
                    f"'{k}' ({p}) does not reside in output_path ({output_path})."
                )

# Backwards compatibility aliases
normalize_project_path = normalize_path
_resolve_project_doc_path = resolve_project_doc_path
_resolve_asset_path = resolve_asset_path
