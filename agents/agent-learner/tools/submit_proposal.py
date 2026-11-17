"""submit_proposal: hands in one candidate change; code checks it and saves it. No file is edited."""
from typing import List, Optional

from typing_extensions import TypedDict

from core.learning.boundaries import ANALYST
from core.learning.proposals import ProposalError, submit
from core.storage.learning import CHECKED, LearningStore
from core.tooling import Report, ToolError, agent_tool, current_agent_id

# Every call writes a row (the proposal), so a read-only run (the voice replay) refuses it.
READ_ACTIONS = ()


class Edit(TypedDict):
    """One exact change: `find` copied from the file, and the text to put in its place."""
    find: str
    replace: str


@agent_tool
def submit_proposal(report: Report, path: str, edits: List[Edit], pattern: str, rationale: str, fixes: List[int],
                    expected: str = "", retires: Optional[List[str]] = None,
                    dropped: Optional[List[str]] = None) -> dict:
    """Hand in one candidate change for review. Code checks it and saves it; no file is edited.

    Reads: the file it changes, the cards it cites and their episodes, and this week's proposals.
    Computes: the static checks (where it may land, that each edit applies, size, budget, protected behaviours, new wiring, personal information, that the file still loads, agent_lint) and saves it as checked or failed, with each check's reason.
    Leaves out: applying it: a checked proposal waits for the user, and Aki applies it once approved.

    Args:
        path: The file to change: an agent's AGENTS.md or SOUL.md, a skill's SKILL.md, a template under core/prompts/templates/, or pkm/agents/<id>/RULES.md or FEEDBACK.md.
        edits: Exact changes, each {find, replace}: find copied from the file and found there once; replace may be "".
        pattern: The pattern it answers, as friction_query names it: "<agent>:<detector>".
        rationale: Two sentences tying the change to the cards' evidence.
        fixes: The card ids it should fix.
        expected: What the agent should do differently in those cards' situations.
        retires: FEEDBACK entries it makes redundant, quoted.
        dropped: Text a deletion drops on purpose (numbers, paths, names).
    """
    try:
        saved = submit(LearningStore(), path=path, edits=list(edits), pattern=pattern, rationale=rationale,
                       fixes=list(fixes), expected=expected, retires=retires or [], dropped=dropped or [],
                       by_agent=current_agent_id() or ANALYST)
    except ProposalError as e:
        raise ToolError(f"Nothing saved: {e}")
    checks = saved["gates"]
    report.read(saved["path"])
    if saved["status"] == CHECKED:
        upshot = (f"Proposal #{saved['id']} passed every check. It waits for the user's review "
                  f"(`!learn proposals {saved['id']}`); Aki applies it once approved.")
    else:
        failed = ", ".join(c["gate"] for c in checks if not c["ok"])
        upshot = (f"Proposal #{saved['id']} failed {failed}. Revise it and hand it in again, or drop it and "
                  "say why. Never word around a protected, capability or personal check.")
    return {"id": saved["id"], "status": saved["status"], "path": saved["path"], "pattern": saved["pattern"],
            "delta_chars": saved["delta_chars"], "checks": checks, "upshot": upshot}
