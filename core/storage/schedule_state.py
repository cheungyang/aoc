"""Per-schedule state: when each schedule last *succeeded*.

`files_changed_since` and friends compare against this, so it is written only
after a successful run — a failed run must leave its work visible to the next
check rather than silently marking it done.

The recorded time is when the successful run *started*, not when it ended: a
file that changed while the run was in flight may have been missed by it, and
must still count as "changed since" next time.

Lives in its own `schedule_state` table in `memory.db`, next to the jobs table.
The `schedule_notices` table beside it remembers which scan problems were
already posted each day, so a restart doesn't post them again. Both tables are
core/storage/schemas/schedule_state.sql.
"""
import time
from typing import Any, Dict, Optional

from core.storage.base import Store
from core.config.settings import Config


class ScheduleState(Store):
    schema = ("schedule_state",)
    pragmas = ("journal_mode = WAL", "synchronous = NORMAL")

    def default_path(self) -> str:
        return Config().memory_db_path

    def get(self, schedule_id: str) -> Dict[str, Any]:
        row = self.select_one("schedule_state", {"schedule_id": schedule_id})
        return dict(row) if row else {}

    def get_last_success(self, schedule_id: str) -> Optional[float]:
        return self.get(schedule_id).get("last_success_at")

    def _upsert(self, schedule_id: str, **values) -> None:
        self.upsert("schedule_state", {"schedule_id": schedule_id, "updated_at": time.time(), **values},
                    "schedule_id")

    def record_success(self, schedule_id: str, started_at: float) -> None:
        """The only way last_success_at moves."""
        self._upsert(schedule_id, last_success_at=started_at, last_run_at=started_at)

    def record_failure(self, schedule_id: str, started_at: float) -> None:
        """Notes the attempt without touching last_success_at."""
        self._upsert(schedule_id, last_run_at=started_at)

    def record_skip(self, schedule_id: str, reason: str, at: Optional[float] = None) -> None:
        self._upsert(schedule_id, last_skip_at=at or time.time(), last_skip_reason=reason)

    def claim_notice(self, schedule_id: str, issue: str, day: str) -> bool:
        """True the first time `issue` is claimed for `schedule_id` on `day`: post it then, not again."""
        row = {"schedule_id": schedule_id, "issue": issue, "day": day, "posted_at": time.time()}
        return self.insert("schedule_notices", row, or_="IGNORE") is not None

    def release_notice(self, schedule_id: str, issue: str, day: str) -> None:
        """Undoes a claim whose post failed, so a later run may post it."""
        self.delete("schedule_notices", {"schedule_id": schedule_id, "issue": issue, "day": day})
