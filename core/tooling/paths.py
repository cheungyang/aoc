"""Paths an agent tool reads and reports: the user's PKM, named the way the agent names it."""
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def pkm_path(*parts: str) -> str:
    """An absolute path in the user's PKM: `pkm_path("wallet", "kitchen", "INVENTORY.md")`.

    Raises ValueError for a path that would leave the PKM (an absolute part, `..`).
    """
    from core.config.settings import Config

    root = os.path.abspath(Config().pkm_dir)
    path = os.path.abspath(os.path.join(root, *parts))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError(f"{os.path.join(*parts) if parts else ''!r} is outside the PKM")
    return path


def display_path(path: str) -> str:
    """`path` as the agent's own tools name it: `pkm/...` in the PKM, repo-relative in this repo.

    Anything else (a relative path, a label such as "Google Health") comes back unchanged.
    Symlinks are resolved first, so `aoc/pkm/...` and the vault's real path read the same.
    """
    if not path or not os.path.isabs(path):
        return path
    from core.config.settings import Config

    real = os.path.realpath(path)
    for root, prefix in ((os.path.realpath(Config().pkm_dir), "pkm"), (os.path.realpath(PROJECT_ROOT), "")):
        if real == root:
            return prefix or "."
        if real.startswith(root + os.sep):
            relative = os.path.relpath(real, root).replace(os.sep, "/")
            return f"{prefix}/{relative}" if prefix else relative
    return path
