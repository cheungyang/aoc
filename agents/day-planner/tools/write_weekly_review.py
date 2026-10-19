"""write_weekly_review: the approved weekly review, saved in the week's note as the Journals plugin keeps it."""
from typing import List

from typing_extensions import TypedDict

from core.tooling import Report, ToolError, agent_tool, get_local_now, pkm_path, require_permission
from agents.day_planner.lib.journal_notes import note_path, weekly_note
from agents.day_planner.lib.note_sections import SectionError
from agents.day_planner.lib.weekly_review import describe, review_week, save_review

WRITE_ACTION = "write"
# Every call writes, so a read-only run (the voice replay) refuses it.
READ_ACTIONS = ()


class NoteSection(TypedDict):
    """A heading of the week's note, and the markdown to put under it."""
    heading: str
    text: str


@agent_tool
def write_weekly_review(report: Report, sections: List[NoteSection], replace: bool = False) -> str:
    """Save the weekly review the user approved in the week's note, in pkm/vault/journals/weekly/.

    Reads: that note, or the weekly template (pkm/templates/Weekly Review.md) when there is none yet.
    Computes: the week under review (the vault week holding the latest Friday, today included) and its note's name; a new note is the template as written, Templater syntax included, with the Journals plugin's keys.
    Leaves out: the wins and the focus themselves: pass them as approved.

    A section is filled only while empty or still the template's A, B, C; other text there fails the call, writes nothing, and comes back in the error.

    Args:
        sections: The headings to fill, as in the note (e.g. "🌱 Next week's focus"), each with the markdown to put under it.
        replace: Overwrite other text too: yours from earlier in this review, or text the user agreed to replace.
    """
    if not any(section["text"].strip() for section in sections):
        raise ToolError("Nothing to write: give the sections their text.")
    today = get_local_now().date()
    start = review_week(today)
    note = weekly_note(start)
    require_permission(WRITE_ACTION, pkm_path(note))
    report.trace(f"week under review: the vault week holding the latest Friday on or before {today.isoformat()}, "
                 f"from {start.isoformat()}; its note is {note_path(note)}")
    try:
        saved = save_review(pkm_path(), start, [(s["heading"], s["text"]) for s in sections], replace, report)
    except SectionError as e:
        hint = ("\nShow the user that text, then call again with the text to keep and replace=true."
                if "already has text" in str(e) else "")
        raise ToolError(f"Nothing written to {note_path(note)}:\n{e}{hint}") from e
    return describe(saved, today)
