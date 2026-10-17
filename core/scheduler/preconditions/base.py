"""What every precondition is: a declaration validated at load, with an `evaluate(ctx)`."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List

from core.scheduler.spec import ScheduleContext, WorkDecision


class PreconditionError(ValueError):
    """A precondition declaration that cannot be built."""


class Precondition(ABC):
    type_name: str = ""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config)
        self.validate()

    def validate(self) -> None:
        """Raises PreconditionError for a declaration that cannot work."""

    @abstractmethod
    def evaluate(self, ctx: ScheduleContext) -> WorkDecision:
        ...

    def _require_str(self, key: str) -> str:
        value = self.config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PreconditionError(f"'{self.type_name}' requires a non-empty '{key}'")
        return value

    def _require_str_list(self, key: str) -> List[str]:
        value = self.config.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value or not all(
            isinstance(v, str) and v.strip() for v in value
        ):
            raise PreconditionError(
                f"'{self.type_name}' requires '{key}' as a non-empty list of strings"
            )
        return value

    def _check_keys(self, allowed: Iterable[str]) -> None:
        unknown = sorted(set(self.config) - set(allowed) - {"type"})
        if unknown:
            raise PreconditionError(
                f"'{self.type_name}' does not take: {', '.join(unknown)}"
            )


class Always(Precondition):
    """Run every time. Allowed only with a stated reason, so it stays honest."""
    type_name = "always"

    def validate(self):
        self._check_keys(["reason"])
        self.reason = self._require_str("reason")

    def evaluate(self, ctx):
        return WorkDecision(True, f"always: {self.reason}")
