"""Scan: a domain check that decides whether the model runs, and briefs it if so."""
from abc import abstractmethod

from core.scheduler.preconditions.base import Precondition, PreconditionError
from core.scheduler.spec import Findings, ScheduleContext, WorkDecision


class Scan(Precondition):
    """A scheduled scan used to wake its agent on every tick and let the model
    find out there was nothing to report. A scan does that check in code first.

    - `scan()` returns `WorkDecision(False, reason)` when there's nothing worth a
      model turn, or `WorkDecision(True, reason, detail=<findings text>)`.
    - The findings are appended to the schedule's prompt
      (`PromptSchedule.build_prompt`), so the agent starts from what the scan
      read instead of reading it again.
    - It may make the same read-only calls the agent's own first step would
      (yfinance, Zillow, Home Assistant): no extra cost, and quiet days skip the
      model turn. Domain logic stays in its own package (`core/knowledge/...`,
      `core/integrations/...`) and takes no scheduler types; `scan()` imports it
      when it runs, so loading agent.json never does.
    - Fails closed, like `sleep_logged_today`: an error skips the run, with the
      error as the reason.

    A scan may also always say yes, to hand a daily run what code can compute
    exactly (`morning_briefing`). Optional `"args": {...}` are passed to `scan()`.
    Each scan lives in a module named after its type.
    """

    def validate(self):
        self._check_keys(["args"])
        self.args = self.config.get("args") or {}
        if not isinstance(self.args, dict):
            raise PreconditionError(f"'{self.type_name}' args must be an object")

    def evaluate(self, ctx: ScheduleContext) -> WorkDecision:
        try:
            decision = WorkDecision.coerce(self.scan(ctx, **self.args))
        except Exception as e:  # fail closed: never pay for a broken check
            return WorkDecision(False, f"{self.type_name} failed ({type(e).__name__}: {e})")
        if decision and isinstance(decision.detail, str) and decision.detail.strip():
            return WorkDecision(True, decision.reason, detail=Findings(self.type_name, decision.detail))
        return decision

    @abstractmethod
    def scan(self, ctx: ScheduleContext, **args) -> WorkDecision:
        """The check itself: no model, only the read-only calls the agent would make."""
