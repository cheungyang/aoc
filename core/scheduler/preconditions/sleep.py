"""sleep_logged_today: the user is awake, and today's push hasn't run yet."""
from typing import Any, Dict

from core.scheduler.preconditions.base import Precondition
from core.scheduler.spec import WorkDecision


class SleepLoggedToday(Precondition):
    """Today's sleep has synced (the user is awake) and this schedule has not
    already succeeded today.

    Evaluated cheapest-first:

    1. Once-a-day gate: `last_success_at` on today's local date -> no. Local
       only; no network call. It is read from the `schedule_state` table in
       `sessions/memory.db`, so it survives restarts. It advances only on a
       *successful* run, so a failed push is retried on the next tick.
    2. Wake check: a Google Health sleep session whose wake date is today,
       not flagged as a nap, and at least `MIN_MINUTES_ASLEEP` long (filters
       out naps and partial syncs).

    Fails closed: any error reading Google Health is a "no" with the error as
    the reason. The runner fails open on exceptions, which here would mean an
    agent turn on every tick whenever the API is down.
    """
    type_name = "sleep_logged_today"
    MIN_MINUTES_ASLEEP = 180

    def validate(self):
        self._check_keys([])

    @staticmethod
    def _tz():
        from core.util.local_time import get_local_timezone
        return get_local_timezone()

    @staticmethod
    def _read_sleep() -> Dict[str, Any]:
        """Today's sleep via the google_health tool's own reader. Patched in tests."""
        from tools.google_health import GoogleHealthClient, read_sleep
        return read_sleep(GoogleHealthClient(), {"days": 1})

    def evaluate(self, ctx):
        from datetime import datetime

        tz = self._tz()
        today = datetime.fromtimestamp(ctx.now, tz).date()
        if ctx.last_success_at is not None:
            if datetime.fromtimestamp(ctx.last_success_at, tz).date() == today:
                return WorkDecision(False, "already triggered today")

        try:
            data = self._read_sleep() or {}
        except Exception as e:  # fail closed: never pay for a broken check
            return WorkDecision(False, f"could not read sleep data ({type(e).__name__}: {e})")

        today_iso = today.isoformat()
        sessions = [s for s in data.get("sessions") or [] if s.get("wake_date") == today_iso]
        # Only drop sessions explicitly flagged as naps; some sources omit the flag.
        sessions = [s for s in sessions if s.get("main_sleep") is not False]
        sessions = [s for s in sessions if (s.get("minutes_asleep") or 0) >= self.MIN_MINUTES_ASLEEP]
        if not sessions:
            return WorkDecision(False, f"no qualifying sleep session ending {today_iso} yet")

        latest = max(sessions, key=lambda s: s.get("end") or "")
        woke = (latest.get("end") or "")[-5:] or "?"
        hours = round((latest.get("minutes_asleep") or 0) / 60, 1)
        return WorkDecision(True, f"woke {woke}, {hours}h asleep")
