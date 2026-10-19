"""Scan: a domain check that decides whether the model runs, and briefs it if so."""
from abc import abstractmethod

from core.scheduler.preconditions.base import Precondition, PreconditionError
from core.scheduler.spec import Findings, Problems, ScheduleContext, WorkDecision
from core.tooling import DataProblem, Report


def _network_error_types() -> tuple:
    import socket

    kinds = [TimeoutError, ConnectionError, socket.gaierror]
    try:
        import requests

        kinds.append(requests.RequestException)
    except ImportError:
        pass
    return tuple(kinds)


def _is_client_error(error: BaseException) -> bool:
    """Whether `error` carries an HTTP 4xx response other than 408, as requests' HTTPError does."""
    status = getattr(getattr(error, "response", None), "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 408


def is_network_error(error: BaseException) -> bool:
    """Whether `error` says the network hiccuped, not that something needs fixing.

    A timeout, a refused or dropped connection, and an HTTP 408 or 5xx response
    are hiccups. Any other HTTP 4xx needs a person, a rejected key (401) or a
    spent quota (429), even though requests raises it as a RequestException.

    The chain is followed (`raise ... from`, and errors raised while handling
    another), because clients wrap network errors in their own: Home
    Assistant's client turns a refused connection into "Could not reach ...".
    The first error in the chain that is either kind decides.
    """
    kinds = _network_error_types()
    seen = set()
    while error is not None and id(error) not in seen:
        if _is_client_error(error):
            return False
        if isinstance(error, kinds):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


def raw_lines(report: Report) -> tuple:
    """(headline, raw lines) for each issue in `report` that kept the lines it couldn't read."""
    return tuple((issue.headline(), tuple(issue.lines)) for issue in report.issues if issue.lines)


class Scan(Precondition):
    """A scheduled scan used to wake its agent on every tick and let the model
    find out there was nothing to report. A scan does that check in code first.

    - `scan(ctx, report)` returns `WorkDecision(False, reason)` when there's
      nothing worth a model turn, or `WorkDecision(True, reason, detail=<findings text>)`.
    - The findings are appended to the schedule's prompt
      (`PromptSchedule.build_prompt`), so the agent starts from what the scan
      read instead of reading it again.
    - It may make the same read-only calls the agent's own first step would
      (yfinance, Zillow, Home Assistant): no extra cost, and quiet days skip the
      model turn. The logic it uses lives in the agent's `lib/` and takes no
      scheduler types; `scan()` imports it when it runs.
    - Fails closed, like `sleep_logged_today`: an error skips the run, with the
      error as the reason.

    It never skips quietly over a problem. What the scan couldn't read goes in
    `report` (`core.tooling.Report`, the one agent tools use):
    - required data missing (`DataProblem`), or an error that isn't a network
      hiccup: the run is skipped and the runner posts the problem in the
      agent's channel, once per issue per day;
    - issues on a quiet day: the same notice, because "nothing to report" may
      only mean "couldn't read it";
    - issues on a day with work: they ride along in the findings, and the agent
      tells the user;
    - a network error (`is_network_error`: a timeout, a dropped connection, an
      HTTP 408 or 5xx) is only logged: the next run retries. Any other HTTP
      4xx, such as a rejected key or a spent quota, is posted.

    A scan may also always say yes, to hand a daily run what code can compute
    exactly (`morning_briefing`). Optional `"args": {...}` are passed to `scan()`.
    Each scan belongs to one agent: `agents/<agent_id>/preconditions/<type>.py`,
    found by `agent_preconditions` and usable only in that agent's schedules.
    """

    def validate(self):
        self._check_keys(["args"])
        self.args = self.config.get("args") or {}
        if not isinstance(self.args, dict):
            raise PreconditionError(f"'{self.type_name}' args must be an object")

    def evaluate(self, ctx: ScheduleContext) -> WorkDecision:
        report = Report()
        try:
            decision = WorkDecision.coerce(self.scan(ctx, report, **self.args))
        except DataProblem as e:
            report.merge(e.report)
            return self._problems(report.issue_lines() or [str(e)], raw_lines(report))
        except Exception as e:  # fail closed: never pay for a broken check
            if is_network_error(e):
                return WorkDecision(False, f"{self.type_name} failed ({type(e).__name__}: {e})")
            return self._problems(report.issue_lines() + [f"the check failed ({type(e).__name__}: {e})"],
                                  raw_lines(report))

        issues = tuple(report.issue_lines())
        if decision:
            text = decision.detail if isinstance(decision.detail, str) else ""
            if text.strip() or issues:
                return WorkDecision(True, decision.reason,
                                    detail=Findings(self.type_name, text, issues, raw=raw_lines(report)))
            return decision
        if issues:
            return WorkDecision(False, f"{decision.reason} (with issues)",
                                detail=Problems(self.type_name, issues, raw=raw_lines(report)))
        return decision

    def _problems(self, issues, raw: tuple = ()) -> WorkDecision:
        issues = tuple(issues)
        return WorkDecision(False, f"{self.type_name} can't run: {'; '.join(issues)}",
                            detail=Problems(self.type_name, issues, raw=raw))

    @abstractmethod
    def scan(self, ctx: ScheduleContext, report: Report, **args) -> WorkDecision:
        """The check itself: no model, only the read-only calls the agent would make.

        Record what couldn't be read in `report`; call `report.raise_if_failed()`
        (or raise `DataProblem`) when required data is missing.
        """
