"""The single Google Health tool.

Reads activity, sleep and body metrics from the Google Health API
(health.googleapis.com/v4, the successor to the Fitbit Web API) and logs
workout sessions. Shaped like `tools/home_assistant.py`: one tool, a batch of
instructions, a fixed action vocabulary, permission bundles declared beside the
actions, and a guard chain that every write passes through.

Sections, in order:

  1. Configuration and credentials
  2. HTTP client (OAuth refresh, retry, redaction)
  3. Action vocabulary and permissions
  4. Date and time helpers
  5. Reads and their summarisers
  6. Write guards and `log_workout`
  7. The tool

Credentials come from `google_health_credentials.json` at the project root
(mode 600, git- and docker-ignored), created once per Google account by
`scripts/google_health_auth.py`.
"""
import difflib
import fnmatch
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from langchain_core.tools import tool

from core.loaders.tools_loader import ToolsLoader
from core.runtime.execution_context import try_context
from core.util import format_tool_response
from core.util.config import Config

# =============================================================================
# 1. Configuration and credentials
# =============================================================================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CREDENTIALS_PATH = os.path.join(PROJECT_ROOT, "google_health_credentials.json")

CREDENTIALS_PATH_ENV_VAR = "GOOGLE_HEALTH_CREDENTIALS_FILE"
TIMEOUT_ENV_VAR = "GOOGLE_HEALTH_REQUEST_TIMEOUT"
WRITE_ENABLED_ENV_VAR = "GOOGLE_HEALTH_WRITE_ENABLED"

API_BASE = "https://health.googleapis.com/v4"
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Least privilege: read only what the actions below expose, write only activity.
# A scope that is never granted cannot be misused, whatever an agent.json says.
# Kept in sync with scripts/google_health_auth.py by a test.
SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
)

AUTH_HINT = (
    "Run `python scripts/google_health_auth.py` on a machine with a browser, or "
    "`docker exec -it <container> python scripts/google_health_auth.py --no-browser`."
)


class GoogleHealthError(Exception):
    """The API could not be reached, refused a request, or is not configured."""


class GuardRejection(Exception):
    """A write was refused before reaching the API. The message is shown to the agent."""


class InstructionError(ValueError):
    """An instruction is malformed (bad dates, missing fields)."""


def resolve_credentials_path(path: Optional[str] = None) -> str:
    raw = path or Config().get(CREDENTIALS_PATH_ENV_VAR) or DEFAULT_CREDENTIALS_PATH
    return os.path.abspath(os.path.expanduser(raw))


def read_credentials(path: Optional[str] = None) -> Dict[str, str]:
    """Reads the OAuth credential file, refusing one that other users can read."""
    resolved = resolve_credentials_path(path)
    if not os.path.exists(resolved):
        raise GoogleHealthError(f"Google Health credentials not found at {resolved}. {AUTH_HINT}")

    mode = os.stat(resolved).st_mode
    if mode & 0o077:
        raise GoogleHealthError(
            f"Google Health credentials file {resolved} is readable by other users "
            f"(mode {oct(mode & 0o777)}). Run: chmod 600 {resolved}"
        )

    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError:
        raise GoogleHealthError(f"Google Health credentials file {resolved} is not valid JSON.") from None

    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
    if missing:
        raise GoogleHealthError(
            f"Google Health credentials file {resolved} is missing {', '.join(missing)}. {AUTH_HINT}"
        )
    return data


def write_enabled() -> bool:
    """Master kill-switch for every mutating action. Off unless set in .env."""
    return str(Config().get(WRITE_ENABLED_ENV_VAR, "")).strip().lower() in {"1", "true", "yes", "on"}


REDACTED = "***REDACTED***"
_BEARER_RE = re.compile(r"Bearer\s+\S+")
_ACCESS_TOKEN_RE = re.compile(r"ya29\.[A-Za-z0-9_\-\.]+")
_REFRESH_TOKEN_RE = re.compile(r"1//[A-Za-z0-9_\-]+")


def redact(text: Any, *secrets: Optional[str]) -> str:
    """Strips known secrets, bearer headers and Google token shapes from a string."""
    if text is None:
        return ""
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, REDACTED)
    out = _BEARER_RE.sub(f"Bearer {REDACTED}", out)
    out = _ACCESS_TOKEN_RE.sub(REDACTED, out)
    return _REFRESH_TOKEN_RE.sub(REDACTED, out)


# =============================================================================
# 2. HTTP client
# =============================================================================

DEFAULT_TIMEOUT = 20
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5
# Refresh this long before expiry so a request started just before it does not
# arrive just after it.
EXPIRY_SKEW_SECONDS = 60

# Access tokens are cached per credentials file for the life of the process, so
# a run of tool calls costs one refresh rather than one per call. Locked because
# LangChain runs sync tools in worker threads.
_token_cache: Dict[str, Tuple[str, float]] = {}
_token_lock = threading.Lock()


def reset_token_cache() -> None:
    """Forgets cached access tokens. For tests."""
    with _token_lock:
        _token_cache.clear()


class GoogleHealthClient:
    """A thin, synchronous REST client for the signed-in user's health data.

    The refresh token and client secret are held privately and sent only in
    POST bodies to Google's token endpoint; access tokens are sent only in an
    Authorization header. Neither appears in a repr, URL or raised message.
    """

    def __init__(
        self,
        credentials: Optional[Dict[str, str]] = None,
        credentials_path: Optional[str] = None,
        timeout: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ):
        self._cache_key = resolve_credentials_path(credentials_path)
        self._credentials = credentials if credentials is not None else read_credentials(credentials_path)
        self.timeout = int(timeout if timeout is not None else Config().get(TIMEOUT_ENV_VAR, DEFAULT_TIMEOUT))
        self._session = session or requests.Session()

    def __repr__(self) -> str:
        return f"<GoogleHealthClient {API_BASE} timeout={self.timeout}s>"

    def _secrets(self):
        return (self._credentials.get("client_secret"), self._credentials.get("refresh_token"))

    def _access_token(self, force_refresh: bool = False) -> str:
        with _token_lock:
            cached = _token_cache.get(self._cache_key)
            if cached and not force_refresh and cached[1] - EXPIRY_SKEW_SECONDS > time.time():
                return cached[0]

        try:
            response = self._session.post(
                self._credentials.get("token_uri") or TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._credentials["client_id"],
                    "client_secret": self._credentials["client_secret"],
                    "refresh_token": self._credentials["refresh_token"],
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GoogleHealthError(
                f"Could not reach Google's OAuth endpoint: {redact(exc, *self._secrets())}"
            ) from None

        body = _parse(response)
        if response.status_code >= 400 or not isinstance(body, dict) or not body.get("access_token"):
            if isinstance(body, dict) and body.get("error") == "invalid_grant":
                raise GoogleHealthError(
                    "Google rejected the refresh token (invalid_grant). It was revoked, or it "
                    "expired: OAuth apps whose consent screen is in 'Testing' status get "
                    "refresh tokens that last only 7 days -- set it to 'In production' to "
                    f"avoid this. {AUTH_HINT}"
                )
            raise GoogleHealthError(
                f"Google OAuth token refresh failed ({response.status_code}): "
                f"{redact(getattr(response, 'text', ''), *self._secrets())[:300]}"
            )

        token = body["access_token"]
        with _token_lock:
            _token_cache[self._cache_key] = (token, time.time() + int(body.get("expires_in", 3600)))
        return token

    def request(
        self,
        method: str,
        path: str,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        retry: bool = True,
    ) -> Any:
        """Performs one API request. `path` is relative to /v4.

        Pass `retry=False` for non-idempotent calls: a lost 503 on a create
        does not say whether the workout was stored, and logging it twice is a
        real corruption of the user's history.
        """
        url = f"{API_BASE}/{path.lstrip('/')}"
        refreshed = False
        last_error: Optional[GoogleHealthError] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            token = self._access_token()
            try:
                response = self._session.request(
                    method=method.upper(),
                    url=url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=json_body,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = GoogleHealthError(
                    f"Could not reach the Google Health API at {url}: {redact(exc, token, *self._secrets())}"
                )
                if retry and attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from None

            # A token can be revoked before its stated expiry. Refresh once and
            # replay; the request was rejected, so replaying cannot duplicate it.
            if response.status_code == 401 and not refreshed:
                refreshed = True
                self._access_token(force_refresh=True)
                continue

            if retry and response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)
                continue

            if response.status_code >= 400:
                raise GoogleHealthError(self._describe_error(method, url, response, token))
            return _parse(response)

        raise last_error or GoogleHealthError(
            f"Google Health API did not respond successfully to {method.upper()} {url} "
            f"after {MAX_ATTEMPTS} attempts."
        )

    def _describe_error(self, method: str, url: str, response, token: str) -> str:
        body = _parse(response)
        detail, status = "", ""
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            detail = body["error"].get("message", "")
            status = body["error"].get("status", "")
        detail = redact(detail or getattr(response, "text", ""), token, *self._secrets())[:500]
        if response.status_code == 403:
            return (
                f"Google Health API refused {method.upper()} {url} (403 {status}): {detail}. "
                f"The granted OAuth scopes may not cover this data, or the Cloud project "
                f"may not have Google Health API access. {AUTH_HINT}"
            )
        return f"Google Health API returned {response.status_code} {status} for {method.upper()} {url}: {detail}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_body: Optional[Any] = None, retry: bool = True) -> Any:
        return self.request("POST", path, json_body=json_body, retry=retry)


def _parse(response) -> Any:
    try:
        return response.json()
    except ValueError:
        return getattr(response, "text", "")


# =============================================================================
# 3. Action vocabulary and permissions
# =============================================================================

# action -> the data type it touches, which is also the permission target. Data
# type ids are the API's own kebab-case names, so a grant reads like the API.
READ_ACTIONS = {
    "active_minutes": "active-minutes",
    "list_exercises": "exercise",
    "sleep": "sleep",
    "weight": "weight",
    "exercise_types": "exercise",
}
WRITE_ACTIONS = {
    "log_workout": "exercise",
}
ACTION_TARGETS = {**READ_ACTIONS, **WRITE_ACTIONS}

# Named bundles for agent.json, e.g. {"*": ["@observe"], "exercise": ["@log"]}.
# Resolved by ToolsLoader; see core/loaders/permission_bundles.py.
PERMISSION_BUNDLES = {
    # Every read. Safe to run unattended.
    "@observe": sorted(READ_ACTIONS),
    # Adding workouts to the user's history. Kept apart from @observe so the two
    # can be granted against different selectors.
    "@log": sorted(WRITE_ACTIONS),
}


def PERMISSION_MATCHER(selector: str, target: str) -> bool:
    """Whether a grant selector covers a data type.

    Targets are data type ids (`exercise`, `sleep`, `weight`, `active-minutes`),
    not paths, so the loader's default path matcher would mis-handle them.
    Globs, so `"*"` covers everything.
    """
    return selector == target or fnmatch.fnmatchcase(target, selector)


# Exercise.ExerciseType from the v4 reference, minus EXERCISE_TYPE_UNSPECIFIED.
# The API spells pickleball "PICKELBALL"; the alias table maps the usual spelling.
EXERCISE_TYPES = frozenset("""
AEROBIC_WORKOUT ARCHERY ASSAULT_BIKE BACKPACKING BADMINTON BALLET BALLROOM_DANCE
BARRE_CLASS BASEBALL BASKETBALL BIKING BILLIARDS BODY_WEIGHT BOOTCAMP BOWLING BOXING
BREAKDANCING CALISTHENICS CANOEING CARDIO_SCULPT CARDIO_WORKOUT CARPENTRY CHEERLEADING
CIRCUIT_TRAINING CLEANING CLIMBING CORE_TRAINING CRICKET CROQUET CROSS_COUNTRY_SKI
CROSS_TRAINING CROSSFIT CURLING DANCING DIVING ELECTRIC_BIKE ELECTRIC_SCOOTER ELLIPTICAL
EQUESTRIAN_SPORTS EXERCISE_CLASS FENCING FIELD_HOCKEY FISHING FITNESS_GAMING FOILING
FOOTBALL_AMERICAN FOOTBALL_AUSTRALIAN FREE_WEIGHTS FRISBEE_PLAYING_GENERAL
FUNCTIONAL_STRENGTH_TRAINING GARDENING GOLF GYMNASTICS HANDBALL HAND_CYCLING HIIT HIKING
HIP_HOP HOCKEY HOEING HOUSEHOLD_CHORES HUNTING ICE_SKATING INCLINE_RUN INCLINE_WALK
INDOOR_CLIMBING INTERVAL_WORKOUT JAZZ_DANCE JIU_JITSU JUMPING_ROPE KARATE KAYAKING
KICKBOXING KITESURFING LACROSSE MARTIAL_ARTS MEDITATE MODERN_DANCE MOTOCROSS MOTORCYCLE
MOUNTAIN_BIKE MOWING_LAWN MUAY_THAI MULTISPORT MUSICAL_PERFORMANCE NORDIC_WALKING
ORIENTEERING OTHER OUTDOOR_BIKE OUTDOOR_WORKOUT PADDLEBOARDING PADEL PAINTING PARAGLIDING
PARKOUR PICKELBALL PILATES POLO POWERLIFTING POWER_WALKING RACKET_SPORTS RACQUETBALL
RESISTANCE_BANDS ROCK_CLIMBING ROLLERBLADING ROLLER_SKATING ROWING ROWING_MACHINE RUCKING
RUGBY RUNNING SAILING SCOOTERING SCUBA_DIVING SHOOTING SHOVELING SKATEBOARDING SKATING
SKIING SKYDIVING SNORKELING SNOWBOARDING SNOWMOBILING SNOWSHOEING SNOW_SPORT SOCCER
SOFTBALL SPEED_SKATING SPINNING SPORT SQUASH STAIRCLIMBER STATIONARY_BIKE STEP_TRAINING
STRENGTH_TRAINING STRETCHING STROLLER_WALK SURFING SWIMMING SWIMMING_OPEN_WATER
SWIMMING_POOL SYNCHRONIZED_SWIMMING TABATA_WORKOUT TABLE_TENNIS TAEKWONDO TAI_CHI TANGO
TENNIS TRACK_AND_FIELD TRAIL_RUN TRAMPOLINE TREADMILL TREADMILL_WALK TRX ULTIMATE_FRISBEE
UNICYCLING VOLLEYBALL VOLLEYBALL_BEACH WAKEBOARDING WALKING WALK_WITH_WEIGHTS
WATER_AEROBICS WATER_JOGGING WATER_POLO WATER_SKIING WATER_SPORT WATER_VOLLEYBALL WEEDING
WEIGHTLIFTING WEIGHT_MACHINES WEIGHTS WHEELCHAIR WINDSURFING WORKOUT WRESTLING YOGA
YOGA_BIKRAM YOGA_HATHA YOGA_POWER YOGA_VINYASA ZUMBA
""".split())

EXERCISE_ALIASES = {
    "RUN": "RUNNING", "JOG": "RUNNING", "JOGGING": "RUNNING",
    "WALK": "WALKING", "HIKE": "HIKING", "SWIM": "SWIMMING",
    "BIKE": "BIKING", "CYCLING": "BIKING", "CYCLE": "BIKING", "BICYCLING": "BIKING",
    "LIFTING": "WEIGHTLIFTING", "WEIGHT_LIFTING": "WEIGHTLIFTING",
    "WEIGHT_TRAINING": "WEIGHTS", "GYM": "WORKOUT",
    "PICKLEBALL": "PICKELBALL", "SPIN": "SPINNING", "ROW": "ROWING",
    "JUMP_ROPE": "JUMPING_ROPE", "STAIRMASTER": "STAIRCLIMBER",
}


def normalise_exercise_type(value: Any) -> str:
    """Maps "strength training", "Run", "STRENGTH_TRAINING" to an API enum value."""
    key = re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")
    key = EXERCISE_ALIASES.get(key, key)
    if key in EXERCISE_TYPES:
        return key
    close = difflib.get_close_matches(key, EXERCISE_TYPES, n=5, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise GuardRejection(
        f"Unknown exercise_type {value!r}.{hint} Use the 'exercise_types' action for the "
        f"full list, or OTHER with a 'display_name'."
    )


# =============================================================================
# 4. Date and time helpers
# =============================================================================

DEFAULT_DAYS = 7
MAX_RANGE_DAYS = 366
# dailyRollUp caps active-minutes ranges at 14 days per request.
ROLLUP_CHUNK_DAYS = 14


def _tz() -> ZoneInfo:
    return ZoneInfo(Config().timezone)


def _now() -> datetime:
    """Current time, aware. A function so tests can pin it."""
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().astimezone(_tz()).date()


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise InstructionError(f"'{field}' must be a date in YYYY-MM-DD form, got {value!r}.") from None


def _date_range(instruction: dict) -> Tuple[date, date]:
    """Resolves a closed-open civil date range from `days` or `start_date`/`end_date`.

    `end_date` is exclusive and defaults to tomorrow, so the range includes today.
    """
    today = _today()
    end = _parse_date(instruction["end_date"], "end_date") if instruction.get("end_date") else today + timedelta(days=1)
    if instruction.get("start_date"):
        start = _parse_date(instruction["start_date"], "start_date")
    else:
        try:
            days = int(instruction.get("days", DEFAULT_DAYS))
        except (TypeError, ValueError):
            raise InstructionError(f"'days' must be an integer, got {instruction.get('days')!r}.") from None
        if days < 1:
            raise InstructionError("'days' must be at least 1.")
        start = end - timedelta(days=days)

    if start >= end:
        raise InstructionError(f"start_date {start} must be before end_date {end} (end is exclusive).")
    if (end - start).days > MAX_RANGE_DAYS:
        raise InstructionError(f"Date range is limited to {MAX_RANGE_DAYS} days.")
    return start, end


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """RFC 3339 from the API -> aware datetime. None when absent or unparseable."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    # Python < 3.11 fromisoformat rejects more than 6 fractional digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_user_time(value: Any, field: str) -> datetime:
    """An agent-supplied time. Naive values are read as the user's local time."""
    if not value:
        raise InstructionError(f"'{field}' is required (ISO 8601, e.g. 2026-10-04T07:30 or with an offset).")
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InstructionError(f"'{field}' is not an ISO 8601 date-time: {value!r}.") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz())


def _local(ts: Optional[datetime]) -> Optional[str]:
    return ts.astimezone(_tz()).strftime("%Y-%m-%d %H:%M") if ts else None


def _civil(d: date) -> dict:
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def _offset(ts: datetime) -> str:
    return f"{int(ts.utcoffset().total_seconds())}s"


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seconds(duration: Any) -> Optional[float]:
    """Protobuf Duration ("3.5s") -> seconds."""
    if not duration:
        return None
    try:
        return float(str(duration).rstrip("s"))
    except ValueError:
        return None


def _compact(row: dict) -> dict:
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


# =============================================================================
# 5. Reads and their summarisers
# =============================================================================

def _list_points(client, data_type: str, flt: str, limit: int, page_size: int) -> Tuple[List[dict], bool]:
    """Pages through dataPoints.list. Returns (points, truncated)."""
    points: List[dict] = []
    token = None
    while len(points) < limit:
        params = {"filter": flt, "pageSize": min(page_size, limit - len(points))}
        if token:
            params["pageToken"] = token
        body = client.get(f"users/me/dataTypes/{data_type}/dataPoints", params=params) or {}
        points.extend(body.get("dataPoints") or [])
        token = body.get("nextPageToken")
        if not token:
            break
    return points[:limit], bool(token)


def _limit(instruction: dict, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(instruction.get("limit", default)), maximum))
    except (TypeError, ValueError):
        raise InstructionError(f"'limit' must be an integer, got {instruction.get('limit')!r}.") from None


def _point_id(point: dict) -> Optional[str]:
    name = point.get("name") or ""
    return name.rsplit("/", 1)[-1] or None


def _range_dict(start: date, end: date) -> dict:
    return {"start_date": start.isoformat(), "end_date_exclusive": end.isoformat()}


def read_active_minutes(client, instruction: dict) -> dict:
    start, end = _date_range(instruction)
    days = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=ROLLUP_CHUNK_DAYS), end)
        body = client.post(
            "users/me/dataTypes/active-minutes/dataPoints:dailyRollUp",
            json_body={"range": {"start": _civil(cursor), "end": _civil(chunk_end)}, "windowSizeDays": 1},
        ) or {}
        days.extend(_summarise_active_day(p) for p in body.get("rollupDataPoints") or [])
        cursor = chunk_end

    days.sort(key=lambda d: d["date"] or "")
    totals = {lvl: sum(d[lvl] for d in days) for lvl in ("light", "moderate", "vigorous", "total")}
    return {
        **_range_dict(start, end),
        "days": days,
        "totals": totals,
        "daily_average_total": round(totals["total"] / len(days), 1) if days else 0,
    }


def _summarise_active_day(point: dict) -> dict:
    d = ((point.get("civilStartTime") or {}).get("date")) or {}
    day = f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}" if {"year", "month", "day"} <= d.keys() else None
    # The union member is looked up by shape rather than name, so a renamed
    # field degrades to zeros instead of a KeyError.
    levels: List[dict] = []
    for value in point.values():
        if isinstance(value, dict) and "activeMinutesRollupByActivityLevel" in value:
            levels = value["activeMinutesRollupByActivityLevel"] or []
            break
    row = {"date": day, "light": 0, "moderate": 0, "vigorous": 0}
    for entry in levels:
        level = str(entry.get("activityLevel", "")).lower()
        if level in row:
            row[level] = _int(entry.get("activeMinutesSum")) or 0
    row["total"] = row["light"] + row["moderate"] + row["vigorous"]
    return row


def read_exercises(client, instruction: dict) -> dict:
    start, end = _date_range(instruction)
    limit = _limit(instruction, default=25, maximum=200)
    flt = (
        f'exercise.interval.civil_start_time >= "{start.isoformat()}" AND '
        f'exercise.interval.civil_start_time < "{end.isoformat()}"'
    )
    points, truncated = _list_points(client, "exercise", flt, limit, page_size=25)
    sessions = [summarise_exercise(p) for p in points]
    return _compact({
        **_range_dict(start, end),
        "count": len(sessions),
        "truncated": truncated or None,
        "total_minutes": sum(s.get("duration_min", 0) for s in sessions),
        "sessions": sessions,
    })


def summarise_exercise(point: dict) -> dict:
    ex = point.get("exercise") or {}
    interval = ex.get("interval") or {}
    metrics = ex.get("metricsSummary") or {}
    start, end = _parse_timestamp(interval.get("startTime")), _parse_timestamp(interval.get("endTime"))
    active = _seconds(ex.get("activeDuration"))
    distance = metrics.get("distanceMillimeters")
    calories = metrics.get("caloriesKcal")
    return _compact({
        "id": _point_id(point),
        "type": ex.get("exerciseType"),
        "name": ex.get("displayName"),
        "start": _local(start),
        "end": _local(end),
        "duration_min": round((end - start).total_seconds() / 60) if start and end else None,
        "active_duration_min": round(active / 60) if active else None,
        "calories_kcal": round(calories) if isinstance(calories, (int, float)) else None,
        "distance_km": round(distance / 1_000_000, 2) if isinstance(distance, (int, float)) else None,
        "avg_heart_rate": _int(metrics.get("averageHeartRateBeatsPerMinute")),
        "steps": _int(metrics.get("steps")),
        "active_zone_minutes": _int(metrics.get("activeZoneMinutes")),
        "recording": (point.get("dataSource") or {}).get("recordingMethod"),
        "notes": ex.get("notes"),
    })


def read_sleep(client, instruction: dict) -> dict:
    """Sleep sessions whose *wake* date falls in the range, plus a per-night total."""
    start, end = _date_range(instruction)
    limit = _limit(instruction, default=50, maximum=200)
    flt = (
        f'sleep.interval.civil_end_time >= "{start.isoformat()}" AND '
        f'sleep.interval.civil_end_time < "{end.isoformat()}"'
    )
    points, truncated = _list_points(client, "sleep", flt, limit, page_size=25)
    sessions = [summarise_sleep(p) for p in points]

    nights: Dict[str, int] = {}
    for s in sessions:
        if s.get("wake_date"):
            nights[s["wake_date"]] = nights.get(s["wake_date"], 0) + s.get("minutes_asleep", 0)
    per_night = [
        {"wake_date": d, "minutes_asleep": m, "hours_asleep": round(m / 60, 2)}
        for d, m in sorted(nights.items())
    ]
    return _compact({
        **_range_dict(start, end),
        "nights": per_night,
        "average_hours_asleep": round(sum(n["minutes_asleep"] for n in per_night) / 60 / len(per_night), 2)
        if per_night else None,
        "truncated": truncated or None,
        "sessions": sessions,
    })


def summarise_sleep(point: dict) -> dict:
    sl = point.get("sleep") or {}
    interval = sl.get("interval") or {}
    summary = sl.get("summary") or {}
    start, end = _parse_timestamp(interval.get("startTime")), _parse_timestamp(interval.get("endTime"))
    stages = {
        str(s.get("type", "")).lower(): _int(s.get("minutes"))
        for s in summary.get("stagesSummary") or []
        if s.get("type")
    }
    return _compact({
        "id": _point_id(point),
        "start": _local(start),
        "end": _local(end),
        "wake_date": end.astimezone(_tz()).date().isoformat() if end else None,
        "main_sleep": (sl.get("metadata") or {}).get("mainSleep"),
        "minutes_asleep": _int(summary.get("minutesAsleep")) or 0,
        "minutes_awake": _int(summary.get("minutesAwake")),
        "minutes_in_bed": _int(summary.get("minutesInSleepPeriod")),
        "minutes_to_fall_asleep": _int(summary.get("minutesToFallAsleep")),
        "stages_min": stages,
    })


KG_TO_LB = 2.2046226218


def read_weight(client, instruction: dict) -> dict:
    start, end = _date_range(instruction)
    limit = _limit(instruction, default=100, maximum=1000)
    flt = (
        f'weight.sample_time.civil_time >= "{start.isoformat()}" AND '
        f'weight.sample_time.civil_time < "{end.isoformat()}"'
    )
    points, truncated = _list_points(client, "weight", flt, limit, page_size=1000)
    rows = sorted((summarise_weight(p) for p in points), key=lambda r: r.get("time") or "")
    rows = [r for r in rows if "kg" in r]
    result = {
        **_range_dict(start, end),
        "count": len(rows),
        "truncated": truncated or None,
        "latest": rows[-1] if rows else None,
        "change_kg": round(rows[-1]["kg"] - rows[0]["kg"], 2) if len(rows) > 1 else None,
        "measurements": rows,
    }
    return _compact(result)


def summarise_weight(point: dict) -> dict:
    w = point.get("weight") or {}
    grams = w.get("weightGrams")
    kg = grams / 1000 if isinstance(grams, (int, float)) else None
    return _compact({
        "time": _local(_parse_timestamp((w.get("sampleTime") or {}).get("physicalTime"))),
        "kg": round(kg, 2) if kg is not None else None,
        "lb": round(kg * KG_TO_LB, 1) if kg is not None else None,
        "notes": w.get("notes"),
        "recording": (point.get("dataSource") or {}).get("recordingMethod"),
    })


def _dispatch_read(action: str, instruction: dict, get_client: Callable[[], GoogleHealthClient]) -> Any:
    if action == "exercise_types":
        return {"exercise_types": sorted(EXERCISE_TYPES), "aliases": EXERCISE_ALIASES}
    if action == "active_minutes":
        return read_active_minutes(get_client(), instruction)
    if action == "list_exercises":
        return read_exercises(get_client(), instruction)
    if action == "sleep":
        return read_sleep(get_client(), instruction)
    if action == "weight":
        return read_weight(get_client(), instruction)
    raise InstructionError(f"Action '{action}' is declared but not implemented.")


# =============================================================================
# 6. Write guards and log_workout
# =============================================================================

MIN_SESSION = timedelta(minutes=1)
MAX_SESSION = timedelta(hours=24)
FUTURE_TOLERANCE = timedelta(minutes=5)
# A workout from further back than this is far more likely to be a year or
# month typo than a genuine late entry.
MAX_BACKDATE = timedelta(days=30)
MAX_NOTES_CHARS = 1000


def authorise_write(ctx, tools_loader: ToolsLoader) -> None:
    """Process- and grant-level gates every write passes, before any validation."""
    if not write_enabled():
        raise GuardRejection(
            f"Writing to Google Health is disabled. Set {WRITE_ENABLED_ENV_VAR}=true in .env "
            f"to enable log_workout."
        )
    # `{}` in agent.json means allow-all to the loader: harmless for reads, not
    # an acceptable way to hand out writes to someone's health record.
    if ctx is not None:
        grant = tools_loader.get_tool_permissions(ctx, "google_health")
        if grant is not None and (not grant or (isinstance(grant, list) and "*" in grant)):
            raise GuardRejection(
                "google_health requires an explicit grant for writes; a blanket grant is "
                "refused. Grant it in agent.json, e.g. {\"exercise\": [\"@log\"]}."
            )


def validate_workout(instruction: dict) -> dict:
    """Checks a log_workout instruction and returns its normalised fields."""
    exercise_type = normalise_exercise_type(instruction.get("exercise_type"))
    start = _parse_user_time(instruction.get("start_time"), "start_time")
    end = _parse_user_time(instruction.get("end_time"), "end_time")
    now = _now()

    if end <= start:
        raise GuardRejection(f"end_time ({_local(end)}) must be after start_time ({_local(start)}).")
    if end - start < MIN_SESSION:
        raise GuardRejection("A workout must last at least one minute.")
    if end - start > MAX_SESSION:
        raise GuardRejection("A workout cannot be longer than 24 hours; check the dates.")
    if end > now + FUTURE_TOLERANCE:
        raise GuardRejection(f"end_time ({_local(end)}) is in the future; only completed workouts can be logged.")
    if start < now - MAX_BACKDATE:
        raise GuardRejection(
            f"start_time ({_local(start)}) is more than {MAX_BACKDATE.days} days ago, which is "
            f"refused as a likely date typo."
        )

    notes = instruction.get("notes")
    if notes is not None and len(str(notes)) > MAX_NOTES_CHARS:
        raise GuardRejection(f"'notes' is limited to {MAX_NOTES_CHARS} characters.")

    display_name = instruction.get("display_name")
    if exercise_type == "OTHER" and not display_name:
        raise GuardRejection("exercise_type OTHER requires a 'display_name' describing the workout.")

    return {
        "exercise_type": exercise_type,
        "start": start,
        "end": end,
        # The API ignores displayName for every type except OTHER and generates
        # its own; it is still a required field, so send a sensible default.
        "display_name": str(display_name) if display_name else exercise_type.replace("_", " ").title(),
        "notes": str(notes) if notes else None,
    }


def find_overlaps(client, start: datetime, end: datetime) -> List[dict]:
    """Existing exercise sessions intersecting [start, end)."""
    tz = _tz()
    lo = start.astimezone(tz).date() - timedelta(days=1)
    hi = end.astimezone(tz).date() + timedelta(days=1)
    flt = (
        f'exercise.interval.civil_start_time >= "{lo.isoformat()}" AND '
        f'exercise.interval.civil_start_time < "{hi.isoformat()}"'
    )
    points, _ = _list_points(client, "exercise", flt, limit=100, page_size=25)
    overlaps = []
    for p in points:
        interval = (p.get("exercise") or {}).get("interval") or {}
        s, e = _parse_timestamp(interval.get("startTime")), _parse_timestamp(interval.get("endTime"))
        if s and e and s < end and start < e:
            overlaps.append(summarise_exercise(p))
    return overlaps


def build_exercise_point(fields: dict) -> dict:
    start, end = fields["start"], fields["end"]
    exercise = {
        "interval": {
            "startTime": start.isoformat(),
            "startUtcOffset": _offset(start),
            "endTime": end.isoformat(),
            "endUtcOffset": _offset(end),
        },
        "exerciseType": fields["exercise_type"],
        "displayName": fields["display_name"],
        "activeDuration": f"{int((end - start).total_seconds())}s",
        # Required by the schema; every member is optional and a manual log has
        # no measured metrics.
        "metricsSummary": {},
    }
    if fields.get("notes"):
        exercise["notes"] = fields["notes"]
    return {"dataSource": {"recordingMethod": "MANUAL"}, "exercise": exercise}


def log_workout(instruction: dict, get_client: Callable[[], GoogleHealthClient], ctx, tools_loader) -> dict:
    """Runs the guard chain, then creates one exercise session.

    Order is deliberate: the cheap absolute refusals (kill switch, blanket
    grant) come before validation, and validation before any network call.
    """
    authorise_write(ctx, tools_loader)
    fields = validate_workout(instruction)
    client = get_client()

    if not instruction.get("allow_overlap"):
        overlaps = find_overlaps(client, fields["start"], fields["end"])
        if overlaps:
            raise GuardRejection(
                "This workout overlaps an existing session, so it was not logged (it may "
                "already be recorded). Existing: " + json.dumps(overlaps, default=str) +
                ". If it is genuinely separate, repeat with \"allow_overlap\": true."
            )

    # No retry: if the response is lost, re-sending could log the workout twice.
    operation = client.post(
        "users/me/dataTypes/exercise/dataPoints", json_body=build_exercise_point(fields), retry=False,
    ) or {}

    created = operation.get("response") if isinstance(operation.get("response"), dict) else {}
    return _compact({
        "logged": {
            "id": _point_id(created) if created else None,
            "exercise_type": fields["exercise_type"],
            "start": _local(fields["start"]),
            "end": _local(fields["end"]),
            "duration_min": round((fields["end"] - fields["start"]).total_seconds() / 60),
        },
        "operation": _compact({"name": operation.get("name"), "done": operation.get("done")}),
    })


# =============================================================================
# 7. The tool
# =============================================================================

def _render(result: Any) -> str:
    return result if isinstance(result, str) else json.dumps(result, indent=2, default=str)


@tool
def google_health(instructions: list[dict]) -> str:
    """Read the user's Google Health (Fitbit / Pixel Watch) data and log workouts.

    Supports several actions in one call. Each instruction is a dict with an
    "action" key plus that action's arguments. Dates are the user's local
    calendar dates (YYYY-MM-DD). Ranges take either "days" (default 7, ending
    today) or "start_date" plus optional "end_date" (exclusive).

    Reading:
      {"action": "active_minutes", "days": 7}
          Light / moderate / vigorous active minutes per day, with totals.
      {"action": "list_exercises", "days": 14, "limit": 25}
          Exercise and workout sessions: type, start/end, duration, calories,
          distance, average heart rate, steps.
      {"action": "sleep", "start_date": "2026-09-28", "end_date": "2026-10-05"}
          Sleep sessions grouped by wake date: hours asleep per night, average,
          and stage minutes per session.
      {"action": "weight", "days": 30}
          Weight measurements (kg and lb), the latest, and the change over the range.
      {"action": "exercise_types"}
          Valid exercise_type values for log_workout.

    Writing:
      {"action": "log_workout", "exercise_type": "STRENGTH_TRAINING",
       "start_time": "2026-10-04T18:00", "end_time": "2026-10-04T18:45",
       "notes": "<optional>", "display_name": "<only for OTHER>"}
          Logs a completed workout. Times without an offset are the user's local
          time. Friendly types like "strength training" or "run" are accepted.
          Refused if it overlaps an existing session (it may already be logged);
          add "allow_overlap": true only if the user confirms it is separate.
          Refused for future times, sessions over 24 h, or starts more than
          30 days ago.

    Args:
        instructions: List of action dicts, executed in order.

    Returns:
        An XML envelope with one <instruction_result> per instruction.
    """
    if not isinstance(instructions, list) or not instructions:
        return format_tool_response(
            "google_health", payload="", errors="'instructions' must be a non-empty list of action dicts."
        )

    ctx = try_context()
    tools_loader = ToolsLoader()
    payload_elements: List[str] = []
    error_elements: List[str] = []

    # One client per call, built only when an action needs the network, so a
    # missing credentials file does not block `exercise_types`.
    holder: Dict[str, GoogleHealthClient] = {}

    def get_client() -> GoogleHealthClient:
        if "client" not in holder:
            holder["client"] = GoogleHealthClient()
        return holder["client"]

    for instruction in instructions:
        if not isinstance(instruction, dict):
            error_elements.append(
                f"<instruction_error>Each instruction must be a dict, got {type(instruction).__name__}.</instruction_error>"
            )
            continue

        action = instruction.get("action")
        if action not in ACTION_TARGETS:
            error_elements.append(
                f'<instruction_error action="{action}">Unknown action. '
                f'Available: {", ".join(sorted(ACTION_TARGETS))}</instruction_error>'
            )
            continue

        target = ACTION_TARGETS[action]
        if ctx is not None and not tools_loader.check_permission(ctx, "google_health", action, target):
            error_elements.append(
                f'<instruction_error action="{action}">Agent {ctx.agent_id} lacks '
                f"permission for '{action}' on {target}.</instruction_error>"
            )
            continue

        try:
            if action in WRITE_ACTIONS:
                result = log_workout(instruction, get_client, ctx, tools_loader)
            else:
                result = _dispatch_read(action, instruction, get_client)
            payload_elements.append(f'<instruction_result action="{action}">{_render(result)}</instruction_result>')
        except (GuardRejection, InstructionError, GoogleHealthError) as exc:
            error_elements.append(f'<instruction_error action="{action}">{exc}</instruction_error>')
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
            error_elements.append(
                f'<instruction_error action="{action}">{type(exc).__name__}: {exc}</instruction_error>'
            )

    return format_tool_response(
        "google_health",
        payload="\n".join(payload_elements),
        errors="\n".join(error_elements) if error_elements else "None",
    )
