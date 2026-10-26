"""The Report a tool fills in, and the envelope every agent tool returns.

Agent tools read files the user edits by hand. When part of a file can't be
read, the tool says so instead of returning a quietly partial answer. The tool
records what it read and what it couldn't in a `Report`, and `@agent_tool`
renders the envelope from it:

    <tool_name_response>
      <status>ok | partial | failed</status>
      <issues>None, or one line per issue, with unrecognized lines indented below it</issues>
      <explain>what was read, and the inputs behind each number (only with explain=True)</explain>
      <payload>the tool's answer</payload>
      <errors>None, or why the call itself failed (a refused permission, a bad argument, a bug)</errors>
    </tool_name_response>

Status and issues come first, so they survive when a long payload is cut.
Shared tools (tools/) have no Report: `format_tool_response` renders theirs,
the same envelope with only the payload and errors.

- `ok`: everything the tool needed was read.
- `partial`: something optional couldn't be read (`report.problem`), or some
  lines weren't recognized (`report.unparsed`). Those lines come back raw, so
  the model can still read them, and the answer covers the rest.
- `failed`: something required is missing (`report.missing`, then
  `report.raise_if_failed()`), or the call errored. There is no answer.

Scans use the same Report: what a scan couldn't read is posted in its agent's
channel instead of being skipped quietly (core/scheduler/preconditions/scan.py).
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional

from core.tooling.paths import display_path

OK = "ok"
PARTIAL = "partial"
FAILED = "failed"

# Rendering caps. What's past them is counted, never dropped silently.
MAX_ISSUES = 30
MAX_RAW_LINES = 40


@dataclass
class Issue:
    """One thing a tool couldn't read: where, what, and the raw lines, if any."""
    source: str
    problem: str
    required: bool = False
    lines: List[str] = field(default_factory=list)

    def headline(self) -> str:
        return f"{self.source}: {self.problem}"

    def render(self) -> str:
        text = f"- {self.headline()}" + (" (required)" if self.required else "")
        shown = self.lines[:MAX_RAW_LINES]
        if shown:
            text += "\n" + "\n".join(f"    {line}" for line in shown)
            if len(self.lines) > len(shown):
                text += f"\n    ... and {len(self.lines) - len(shown)} more lines"
        return text


@dataclass
class SourceRead:
    """A file or service a tool read, listed in explain mode."""
    source: str
    modified: Optional[str] = None
    records: Optional[int] = None
    skipped: int = 0

    def render(self) -> str:
        details = []
        if self.modified:
            details.append(f"modified {self.modified}")
        if self.records is not None:
            details.append(f"{self.records} record{'' if self.records == 1 else 's'}")
        if self.skipped:
            details.append(f"{self.skipped} line{'' if self.skipped == 1 else 's'} skipped")
        return f"- {self.source}" + (f" ({', '.join(details)})" if details else "")


class DataProblem(Exception):
    """Required data is missing or unreadable. Carries the report that says why.

    `@agent_tool` turns it into a `failed` envelope; a scan turns it into a
    notice in its agent's channel.
    """

    def __init__(self, report: "Report"):
        self.report = report
        super().__init__("; ".join(report.issue_lines()) or "required data is missing")


class Report:
    """What a tool read and what it couldn't.

    `source` is a file path or a label. An absolute path is shown the way the
    agent names it (`pkm/wallet/...`), so pass the path you opened.
    `explain` is True when the caller asked for explain mode.
    """

    def __init__(self, explain: bool = False):
        self.explain = explain
        self.issues: List[Issue] = []
        self.reads: List[SourceRead] = []
        self.traces: List[str] = []

    # ----------------------------------------------------------- recording

    def missing(self, source: str, problem: str) -> Issue:
        """Something required is missing: the tool can't answer (`failed`)."""
        return self._add(Issue(display_path(source), problem, required=True))

    def problem(self, source: str, problem: str) -> Issue:
        """Something optional couldn't be read; the answer covers the rest (`partial`)."""
        return self._add(Issue(display_path(source), problem))

    def unparsed(self, source: str, lines: Iterable[str]) -> Optional[Issue]:
        """Lines no rule recognized, returned raw so the model can still read them (`partial`).

        Blank lines don't count; with nothing left there is no issue.
        """
        kept = [line.rstrip() for line in lines if line and line.strip()]
        if not kept:
            return None
        noun = "line" if len(kept) == 1 else "lines"
        return self._add(Issue(display_path(source), f"{len(kept)} {noun} not recognized", lines=kept))

    def read(self, source: str, records: Optional[int] = None, skipped: int = 0) -> None:
        """Notes a source read, for explain mode. A file path adds the file's modified time."""
        modified = None
        if source and os.path.isabs(source) and os.path.exists(source):
            from core.util.local_time import get_local_timezone

            mtime = datetime.fromtimestamp(os.path.getmtime(source), get_local_timezone())
            modified = mtime.strftime("%Y-%m-%d %H:%M")
        self.reads.append(SourceRead(display_path(source), modified, records, skipped))

    def trace(self, line: str) -> None:
        """The inputs behind a computed number, for explain mode."""
        self.traces.append(line)

    def merge(self, other: Optional["Report"]) -> "Report":
        """Adds another report's issues, reads and traces to this one."""
        if other is not None and other is not self:
            self.issues.extend(other.issues)
            self.reads.extend(other.reads)
            self.traces.extend(other.traces)
        return self

    def raise_if_failed(self) -> None:
        """Stops the caller with `DataProblem` when something required is missing."""
        if self.status == FAILED:
            raise DataProblem(self)

    def _add(self, issue: Issue) -> Issue:
        self.issues.append(issue)
        return issue

    # ------------------------------------------------------------- reading

    @property
    def status(self) -> str:
        if any(issue.required for issue in self.issues):
            return FAILED
        return PARTIAL if self.issues else OK

    def issue_lines(self) -> List[str]:
        """One line per issue, without the raw lines."""
        return [issue.headline() for issue in self.issues]

    def render_issues(self) -> str:
        if not self.issues:
            return "None"
        shown = [issue.render() for issue in self.issues[:MAX_ISSUES]]
        if len(self.issues) > MAX_ISSUES:
            shown.append(f"- ... and {len(self.issues) - MAX_ISSUES} more issues")
        return "\n".join(shown)

    def render_explain(self) -> str:
        parts = []
        if self.reads:
            parts.append("Read:\n" + "\n".join(read.render() for read in self.reads))
        if self.traces:
            parts.append("Computed:\n" + "\n".join(f"- {line}" for line in self.traces))
        return "\n".join(parts) if parts else "Nothing recorded."


def render_envelope(tool_name: str, report: Report, payload: str = "", errors: Optional[str] = None) -> str:
    """The envelope `@agent_tool` returns. `errors` means the call itself failed: status `failed`."""
    status = FAILED if errors else report.status
    parts = [
        f"<{tool_name}_response>",
        f"  <status>{status}</status>",
        f"  <issues>{report.render_issues()}</issues>",
    ]
    if report.explain:
        parts.append(f"  <explain>{report.render_explain()}</explain>")
    parts += [
        f"  <payload>{payload}</payload>",
        f"  <errors>{errors or 'None'}</errors>",
        f"</{tool_name}_response>",
    ]
    return "\n".join(parts)


def format_tool_response(tool_name: str, payload: str, errors: str = "None") -> str:
    """The envelope a shared tool (tools/) returns: no Report, so only the payload and errors."""
    return f"""<{tool_name}_response>
  <payload>{payload}</payload>
  <errors>{errors}</errors>
</{tool_name}_response>"""
