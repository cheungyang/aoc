"""
Unified path resolution, canonicalization, and validation utilities
for content creation workflows.
"""

import os
import glob
import re
import shutil
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
    """Normalizes path into a clean absolute path.
    Also handles PKM directory prefixing if present."""
    if not path or not str(path).strip():
        return ""
    norm = os.path.normpath(str(path).strip())
    if not os.path.isabs(norm):
        try:
            from core.util.config import Config
            pkm_dir = Config().pkm_dir
            if pkm_dir:
                cand = os.path.join(pkm_dir, norm)
                if os.path.exists(cand):
                    return os.path.abspath(cand)
        except Exception:
            pass
        return os.path.abspath(norm)
    return os.path.abspath(norm)


def archive_asset_for_revision(file_path: str) -> Optional[str]:
    """
    Archives a rejected asset on disk before generating a new version.
    Renames file_path -> {stem}_v{next_v}{ext} where next_v = max(existing_v) + 1 (>= 1).
    Also archives companion .json sidecar if present.
    """
    if not file_path or not (os.path.isfile(file_path) and os.path.getsize(file_path) > 0):
        return None

    parent_dir = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    stem, ext = os.path.splitext(filename)

    # Search for existing versioned archives e.g. horse_image_v1.jpg, horse_image_v2.jpg
    pattern = os.path.join(parent_dir, f"{stem}_v*{ext}")
    existing_files = glob.glob(pattern)

    max_v = 0
    for f in existing_files:
        base = os.path.basename(f)
        m = re.search(r'_v(\d+)\.', base)
        if m:
            v = int(m.group(1))
            if v > max_v:
                max_v = v

    next_v = max_v + 1
    archived_path = os.path.join(parent_dir, f"{stem}_v{next_v}{ext}")
    try:
        shutil.move(file_path, archived_path)
    except Exception as e:
        print(f"paths: Error archiving asset '{file_path}' to '{archived_path}': {e}")
        return None

    # Also archive companion JSON sidecar if it exists
    json_path = os.path.join(parent_dir, f"{stem}.json")
    if os.path.isfile(json_path):
        archived_json = os.path.join(parent_dir, f"{stem}_v{next_v}.json")
        try:
            shutil.move(json_path, archived_json)
        except Exception:
            pass

    return archived_path


def resolve_asset_path(
    output_path: Optional[str],
    topic: str,
    asset_type: str,
    next_version: bool = False
) -> str:
    """
    Returns the deterministic canonical active asset path under output_path.
    In the Archive-on-Reject model, the active asset path is invariant.
    """
    topic = str(topic).strip().lower()
    out_dir = normalize_path(output_path)
    ext = ASSET_EXTENSION_MAP.get(asset_type, "dat")
    if not out_dir:
        return f"{topic}_{asset_type}.{ext}"
    return os.path.join(out_dir, f"{topic}_{asset_type}.{ext}")


def resolve_task_asset(
    output_path: Optional[str],
    topic: str,
    asset_type: str,
    needs_revision: bool = False
) -> tuple[str, bool]:
    """
    Resolves task asset target path and handles Archive-on-Reject versioning.
    Returns:
        (canonical_target_path, should_generate)
    If needs_revision is True:
        Archives any existing file at canonical_target_path to _v{N} on disk,
        and returns (canonical_target_path, True).
    If needs_revision is False and file exists with non-zero size:
        Returns (canonical_target_path, False) [reuse].
    Otherwise:
        Returns (canonical_target_path, True).
    """
    out_dir = normalize_path(output_path)
    canonical_path = resolve_asset_path(out_dir, topic, asset_type)

    if needs_revision:
        archive_asset_for_revision(canonical_path)
        return canonical_path, True

    if os.path.isfile(canonical_path) and os.path.getsize(canonical_path) > 0:
        return canonical_path, False

    return canonical_path, True


def bind_canonical_paths(project_path: str, output_path: str, topic: str) -> Dict[str, str]:
    """
    Deterministically binds and validates canonical absolute paths at Node 1 / Ingress.
    Throws ValueError if either project_path or output_path is missing.
    """
    if not project_path or not str(project_path).strip():
        raise ValueError("Missing required path: 'project_path' must be explicitly provided.")
    if not output_path or not str(output_path).strip():
        raise ValueError("Missing required path: 'output_path' must be explicitly provided.")

    abs_project = normalize_path(project_path)
    abs_output = normalize_path(output_path)
    topic_clean = str(topic or "scene").strip().lower()

    return {
        "project_path": abs_project,
        "output_path": abs_output,
        "topic": topic_clean,
        "image_path": os.path.join(abs_output, f"{topic_clean}_image.jpg"),
        "video_plot_path": os.path.join(abs_output, f"{topic_clean}_video_plot.md"),
        "raw_video_path": os.path.join(abs_output, f"{topic_clean}_raw_video.mp4"),
        "video_path": os.path.join(abs_output, f"{topic_clean}_video.mp4"),
        "remixed_video_path": os.path.join(abs_output, f"{topic_clean}_video.mp4"),
        "copy_path": os.path.join(abs_output, f"{topic_clean}_copy.md"),
        "audio_path": os.path.join(abs_output, f"{topic_clean}_wav.wav"),
        "execution_log_path": os.path.join(abs_output, "execution_log.md"),
        "manifest_path": os.path.join(abs_project, "01_Project_Manifest.md"),
        "creator_instructions_path": os.path.join(abs_project, "02_Creator_Instructions.md"),
        "qc_playbook_path": os.path.join(abs_project, "03_QC_Playbook.md"),
    }


def resolve_project_doc_path(doc_path: Optional[str], project_path: Optional[str], default_filename: str) -> str:
    """Resolves project documentation files (manifest, creator instructions, QC playbook) under project_path."""
    pdir = normalize_path(project_path) if project_path else ""
    if doc_path and str(doc_path).strip():
        d = str(doc_path).strip()
        if os.path.isabs(d):
            return d
        return os.path.join(pdir, d) if pdir else normalize_path(d)
    return os.path.join(pdir, default_filename) if pdir else default_filename


def extract_aspect_ratio_from_instructions(
    instructions_text: str,
    default_aspect_ratio: str = "16:9"
) -> str:
    """Extracts aspect ratio configuration dynamically from creator instructions or manifest markdown."""
    if not instructions_text:
        return default_aspect_ratio

    m = re.search(r'(?:aspect_ratio|aspect|dimensions?)[:=]\s*["\']?([0-9]+:[0-9]+)["\']?', instructions_text, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m_format = re.search(r'(?:format|video format|output format|orientation)[:=]\s*([^\n\r]+)', instructions_text, re.IGNORECASE)
    if m_format:
        val = m_format.group(1).lower()
        if "9:16" in val or "vertical" in val or "portrait" in val or "reels" in val or "shorts" in val or "tiktok" in val:
            return "9:16"
        if "16:9" in val or "horizontal" in val or "landscape" in val or "youtube" in val:
            return "16:9"

    if re.search(r'\b9:16\b', instructions_text):
        return "9:16"
    if re.search(r'\b16:9\b', instructions_text):
        return "16:9"

    return default_aspect_ratio


def load_project_context(
    project_path: Optional[str],
    style: str = "3D",
    manifest_path: Optional[str] = "",
    creator_instructions_path: Optional[str] = ""
) -> Dict[str, Any]:
    """Loads project guidelines, style-specific character sheet, reference image, and aspect ratio from project_path."""
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
    """Validates that output_path exists and all asset paths reside under output_path."""
    output_path = state.get("output_path")
    if not output_path:
        from graphs.content_creation.utils.invariants import AssetInvariantError
        raise AssetInvariantError(f"Missing required output_path at '{node_name}'.")

    norm_output = normalize_path(output_path)
    path_keys = ["image_path", "video_plot_path", "raw_video_path", "remixed_video_path", "copy_path"]
    for k in path_keys:
        p = state.get(k)
        if p and isinstance(p, str):
            norm_p = normalize_path(p)
            if not (norm_p.startswith(norm_output + os.sep) or norm_p == norm_output or os.path.dirname(norm_p) == norm_output):
                from graphs.content_creation.utils.invariants import AssetInvariantError
                raise AssetInvariantError(
                    f"Path mismatch between nodes at '{node_name}': "
                    f"'{k}' ({p}) does not reside in output_path ({output_path})."
                )

# Backwards compatibility helper functions
normalize_project_path = normalize_path
_resolve_project_doc_path = resolve_project_doc_path
_resolve_asset_path = resolve_asset_path
canonicalize_output_path = lambda project_path, output_path, topic="": normalize_path(output_path) if output_path else ""
resolve_under_project = lambda project_path, target_path, default_subpath="": resolve_project_doc_path(target_path, project_path, default_subpath)
