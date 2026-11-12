"""Precondition evaluator for file existence (files_exist).

Evaluates whether specified files exist (or do not exist if invert=True).
"""

import glob
import os
from pathlib import Path
from typing import Any, Dict, List, Union


def _check_files_exist(paths: Union[List[Union[str, Path]], str, Path]) -> bool:
    """Check if at least one matching non-hidden file exists for the given path(s)."""
    if isinstance(paths, (str, Path)):
        paths_list = [paths]
    elif isinstance(paths, (list, tuple, set)):
        paths_list = list(paths)
    else:
        paths_list = []

    for pattern in paths_list:
        pattern_str = str(pattern)
        expanded = os.path.expanduser(pattern_str)

        if os.path.isfile(expanded):
            if not os.path.basename(expanded).startswith("."):
                return True
        elif os.path.isdir(expanded):
            try:
                for entry in os.scandir(expanded):
                    if entry.is_file() and not entry.name.startswith("."):
                        return True
            except OSError:
                pass

        matches = glob.glob(expanded, recursive=True)
        for m in sorted(matches):
            if os.path.isfile(m) and not os.path.basename(m).startswith("."):
                return True

    return False


def evaluate(
    config_or_paths: Union[Dict[str, Any], List[Union[str, Path]], str, Path, None] = None,
    paths: Union[List[Union[str, Path]], str, Path, None] = None,
    invert: Union[bool, None] = None,
    **kwargs: Any,
) -> bool:
    """Evaluate files_exist precondition.

    Supports configuration dictionary (e.g., {"type": "files_exist", "paths": [...], "invert": True})
    or direct parameters (paths, invert).
    """
    if isinstance(config_or_paths, dict):
        config = config_or_paths
        raw_paths = config.get("paths", config.get("path", []))
        invert_val = bool(config.get("invert", False))
    else:
        raw_paths = config_or_paths if paths is None else paths
        invert_val = bool(invert) if invert is not None else False

    if raw_paths is None:
        raw_paths = []

    result = _check_files_exist(raw_paths)

    if invert_val:
        return not result
    return result
