import os
from typing import Optional

def normalize_project_path(path: Optional[str]) -> str:
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
                    # We want to return a path relative to the workspace, 
                    # utilizing the local 'pkm' symlink if possible, so that 
                    # Obsidian tools (which expect pkm/...) and filesystem tools 
                    # (which run from workspace root) both agree.
                    local_pkm = "pkm"
                    if os.path.exists(os.path.join(local_pkm, norm)):
                        return os.path.join(local_pkm, norm)
                    return cand
        except Exception:
            pass
    return norm


def _resolve_project_doc_path(doc_path: Optional[str], project_dir: Optional[str], default_filename: str) -> str:
    """Resolves instruction document path strictly under project_dir."""
    pdir = normalize_project_path(project_dir)
    if not pdir:
        return doc_path or ""
    if doc_path:
        norm_doc = normalize_project_path(doc_path)
        if norm_doc.startswith(pdir):
            return norm_doc
        return os.path.join(pdir, os.path.basename(doc_path))
    return os.path.join(pdir, default_filename)


def _resolve_asset_path(output_dir: Optional[str], topic: str, asset_type: str, next_version: bool = False) -> str:
    """
    Resolves deterministic versioned asset path strictly under output_dir.
    If next_version is True, it increments the version counter for a new file.
    If next_version is False, it returns the highest existing version (or v1 if none).
    """
    import glob
    import re
    topic = str(topic).strip().lower()
    out_dir = normalize_project_path(output_dir)
    ext_map = {
        "image": "jpg",
        "video_plot": "md",
        "raw_video": "mp4",
        "visual_plate": "mp4",
        "video": "mp4",
        "copy": "md",
        "audio": "wav"
    }
    ext = ext_map.get(asset_type, "dat")
    if not out_dir:
        return f"{topic}_{asset_type}.{ext}"

    pattern = os.path.join(out_dir, f"{topic}_{asset_type}*.{ext}")
    existing = glob.glob(pattern)
    
    max_v = 0
    for f in existing:
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
        target_v = max_v + 1
    else:
        target_v = max_v if max_v > 0 else 1
        
    v_str = f"_v{target_v}" if target_v > 1 else ""
    return os.path.join(out_dir, f"{topic}_{asset_type}{v_str}.{ext}")
