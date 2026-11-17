"""Proposals: what the analyst hands in, checked by code and saved. Nothing is edited.

`submit` is all `submit_proposal` (agents/agent-learner/tools/) does:

    1. refuses what isn't a proposal at all: no edits, no rationale, cards that
       don't exist, a pattern not written "<agent>:<detector>", one candidate too
       many (LIMITS), or the same change twice
    2. reads the file as it is now, and the cited cards' episodes (digests.py)
    3. runs the static checks (gates.py)
    4. saves it in memory.db's proposals table: `checked` when every check
       passed, `failed` otherwise, with each check's reason

A checked proposal waits for review. It's applied by agent-designer once you
approve it (a later step), never by the analyst: its tools can't write a file
(core/learning/boundaries.py).
"""
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core.learning import digests, gates
from core.learning.boundaries import ANALYST, in_vault, repo_path
from core.storage.learning import CHECKED, FAILED, LearningStore

WEEK = 7 * 86400
# How much the analyst may hand in: patterns with a checked proposal in 7 days, and
# candidates for one pattern (proposal §5.1 drafts 2-3 per pattern for the replay).
PATTERNS_PER_WEEK = 3
CANDIDATES_PER_PATTERN = 3
MAX_EDITS = 6
MAX_FIXES = 20

_PATTERN = re.compile(r"^([a-z0-9-]+|\?):([a-z_]+)$")


class ProposalError(ValueError):
    """What makes a hand-in not a proposal at all: nothing is saved, and the message says why."""


def _vault_path(path: str, pkm: Optional[str]) -> str:
    if pkm is None:
        from core.tooling import pkm_path
        pkm = str(pkm_path())
    return os.path.join(pkm, path.split("/", 1)[1])


def read_target(path: str, root: str = gates.REPO, pkm: Optional[str] = None) -> str:
    """The file's text now; ProposalError when it doesn't exist."""
    clean = repo_path(path)
    full = _vault_path(clean, pkm) if in_vault(clean) else os.path.join(root, clean)
    try:
        with open(full, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise ProposalError(f"{clean} doesn't exist: a proposal changes a file that's there")
    except (OSError, UnicodeDecodeError) as e:
        raise ProposalError(f"{clean} can't be read: {type(e).__name__}")


def _clean_edits(edits: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    if not edits:
        raise ProposalError("no edits: give each change as {find, replace}")
    if len(edits) > MAX_EDITS:
        raise ProposalError(f"{len(edits)} edits; at most {MAX_EDITS}: a proposal is one small change")
    cleaned = []
    for n, edit in enumerate(edits, 1):
        find, replace = edit.get("find"), edit.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str):
            raise ProposalError(f"edit {n} needs `find` and `replace`, both text ('' replaces with nothing)")
        cleaned.append({"find": find, "replace": replace})
    return cleaned


def _limits(store: LearningStore, pattern: str, path: str, edits: List[Dict[str, str]], now: float) -> None:
    recent = store.proposals(since=now - WEEK, status=CHECKED)
    patterns = {p["pattern"] for p in recent}
    if pattern not in patterns and len(patterns) >= PATTERNS_PER_WEEK:
        raise ProposalError(f"{len(patterns)} patterns already have a checked proposal this week "
                            f"(at most {PATTERNS_PER_WEEK}): {', '.join(sorted(patterns))}")
    same = [p for p in recent if p["pattern"] == pattern]
    if len(same) >= CANDIDATES_PER_PATTERN:
        raise ProposalError(f"{pattern} already has {len(same)} checked candidates this week "
                            f"(at most {CANDIDATES_PER_PATTERN})")
    for p in same:
        if p["path"] == path and p["edits"] == edits:
            raise ProposalError(f"the same change is already proposal #{p['id']}")


def submit(store: LearningStore, *, path: str, edits: Sequence[Mapping[str, Any]], pattern: str,
           rationale: str, fixes: Sequence[int], expected: str = "", retires: Sequence[str] = (),
           dropped: Sequence[str] = (), by_agent: str = ANALYST, session_id: str = "",
           now: Optional[float] = None, root: str = gates.REPO, pkm: Optional[str] = None,
           **check_overrides: Any) -> Dict[str, Any]:
    """Checks and saves one proposal (module docstring); what was saved, with each check's
    result. Raises ProposalError for a hand-in that isn't a proposal."""
    now = time.time() if now is None else now
    clean = repo_path(path)
    if not clean:
        raise ProposalError(f"{path!r} isn't a path in the repo or the vault")
    pattern = (pattern or "").strip()
    if not _PATTERN.match(pattern):
        raise ProposalError(f"pattern {pattern!r} isn't \"<agent>:<detector>\", as friction_query names it")
    if not (rationale or "").strip():
        raise ProposalError("no rationale: say in two sentences how the change answers the cards")
    cleaned = _clean_edits(edits)
    ids = sorted({int(i) for i in fixes or []})
    if not ids:
        raise ProposalError("no cards: cite the friction cards it should fix in `fixes`")
    if len(ids) > MAX_FIXES:
        raise ProposalError(f"{len(ids)} cards; cite at most {MAX_FIXES}, the clearest")
    cards = store.friction_by_ids(ids)
    missing = sorted(set(ids) - {c["id"] for c in cards})
    if missing:
        raise ProposalError(f"no card {', '.join(f'#{i}' for i in missing)}")
    _limits(store, pattern, clean, cleaned, now)

    reason = gates.check_where(gates.Draft(path=clean, before="", edits=cleaned))
    before = read_target(clean, root, pkm) if reason.ok else ""
    cases = digests.as_text([digests.digest(store, card) for card in cards])
    draft = gates.Draft(path=clean, before=before, edits=cleaned, dropped=[str(d) for d in dropped or []],
                        cases=cases, root=root, pkm=pkm)
    checks = gates.run(draft, **check_overrides)
    status = CHECKED if all(c.ok for c in checks) else FAILED
    row = {
        "by_agent": by_agent, "session_id": session_id, "pattern": pattern, "path": clean,
        "base": gates.sha(before), "edits": cleaned, "rationale": rationale.strip(),
        "expected": (expected or "").strip(), "fixes": ids, "retires": [str(r) for r in retires or []],
        "dropped": draft.dropped, "delta_chars": len(draft.after) - len(before) if draft.after else 0,
        "status": status, "gates": [c.row() for c in checks],
    }
    proposal_id = store.add_proposal(row, at=now)
    return {"id": proposal_id, **row}
