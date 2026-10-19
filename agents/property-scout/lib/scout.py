"""The property scout run: criteria.md, one Zillow job for the whole target area, the hard filters, the archive.

Runs through the same RapidAPI service as the shared zillow_query tool, reusing
its URL builder and its price/sqft and cap-rate enrichment. The
property_screen tool and scan both call `run_screen`, and record what they
couldn't read in the same `Report`.

Every request to that service counts against the RapidAPI plan's monthly
quota, the job's result reads included: the quota ran out on a result read on
2026-09-11. So one job covers every target city, and polling waits before its
first read and reads sparsely (`Polling`). The weekly scan's budget, nine
requests, is a hard limit the user set (`SCAN_REQUESTS`), not a preference.
"""
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from core.tooling import Report
from core.util.config import Config  # the RapidAPI key; core.tooling has no settings
from tools.zillow_query import BASE_URL, RAPIDAPI_HOST, _build_search_urls, _enrich_property
from agents.property_scout.lib.archive import load_archive
from agents.property_scout.lib.criteria import Criteria, criteria_path, load_criteria, with_overrides
from agents.property_scout.lib.listings import Screen, screen
from agents.property_scout.lib.money import with_currency

DONE = ("complete", "failed", "finished")


@dataclass(frozen=True)
class Polling:
    """How a Zillow job is awaited: one submit, then at most `max_reads` result reads."""
    first_wait: float
    interval: float
    max_reads: int

    @property
    def requests(self) -> int:
        """The most requests one search makes: the submit and every read."""
        return 1 + self.max_reads

    def read_times(self) -> List[float]:
        """Seconds of waiting before each result read, counted from the submit."""
        return [self.first_wait + self.interval * n for n in range(self.max_reads)]


# A voice or chat turn: reads at about 8, 14, ... 56 s.
TOOL_POLLING = Polling(first_wait=8, interval=6, max_reads=9)
# The weekly scan isn't waited on by anyone: reads at 15, 30, ... 120 s, within
# its budget of nine requests a week, the submit included.
SCAN_REQUESTS = 9
SCAN_POLLING = Polling(first_wait=15, interval=15, max_reads=SCAN_REQUESTS - 1)


class JobPending(Exception):
    """Zillow was still working on the job after the last read."""

    def __init__(self, job_id: str, reads: int):
        super().__init__(f"Zillow job {job_id} still processing after {reads} result reads")
        self.job_id = job_id


@dataclass
class ScreenRun:
    """One screen: what the tool returns and what the scan briefs the agent with. Rows keep plain numbers."""
    criteria: Criteria
    to_judge: Tuple[str, ...]  # criteria.md's lines that aren't filters, as written
    unread: Tuple[str, ...]  # criteria.md's filter lines that didn't read: not applied
    screen: Screen
    matches: List[Dict[str, Any]]  # new matches, or every match with include_evaluated
    already_evaluated: Optional[int] = None  # None: the archive wasn't checked
    listings: Optional[List[Dict[str, Any]]] = None  # with urls: every listing, each with `missed`

    @property
    def pulled(self) -> int:
        return self.screen.pulled

    def payload(self) -> Dict[str, Any]:
        """The property_screen tool's JSON, money written "USD 849,000"."""
        out: Dict[str, Any] = {"criteria": with_currency(self.criteria.in_force()), "pulled": self.pulled}
        if self.listings is not None:
            out["listings"] = [with_currency(row) for row in self.listings]
        else:
            if self.already_evaluated is not None:
                out["already_evaluated"] = self.already_evaluated
            out["matches"] = [with_currency({k: v for k, v in row.items() if k != "missed"}) for row in self.matches]
            out["failed_by_rule"] = self.screen.failed_by_rule
            out["near_misses"] = [with_currency(row) for row in self.screen.near_misses]
        out["criteria_to_judge"] = list(self.to_judge)
        return out


def run_screen(
    report: Report,
    criteria: Optional[Dict[str, Any]] = None,
    urls: Optional[Union[List[str], str]] = None,
    job_id: str = "",
    include_evaluated: bool = False,
    polling: Polling = TOOL_POLLING,
) -> ScreenRun:
    """One screen; what it couldn't read goes in `report`.

    Raises DataProblem when required data is missing (no criteria file, nowhere
    to search, no listings back), ValueError for unusable arguments or
    configuration (before any request), JobPending when Zillow is still
    working, and requests errors from the API.
    """
    parsed = load_criteria(report)
    filters = with_overrides(parsed.criteria, criteria) if criteria else parsed.criteria
    listing_urls = _as_list(urls)
    if not listing_urls and not job_id:
        problem = filters.area_problem()
        if problem:
            report.missing(criteria_path(), problem)
    report.raise_if_failed()
    headers = _headers()

    if job_id:
        job_id = job_id.strip()
        data = _read(headers, job_id)
        if data.get("status") not in DONE:
            raise JobPending(job_id, 1)
        report.trace(f"Zillow: 1 request, a result read of job {job_id}")
    else:
        job_id = _submit(headers, {"urls": listing_urls or search_urls(filters)})
        data, reads = _wait(headers, job_id, polling)
        report.trace(f"Zillow: {1 + reads} requests, 1 submit and {reads} result reads (at most {polling.requests})")

    props = [
        _enrich_property(item["property"])
        for item in data.get("results") or []
        if isinstance(item, dict) and isinstance(item.get("property"), dict)
    ]
    report.read(f"Zillow job {job_id}", records=len(props))
    errors = data.get("errors") or []
    for error in errors:
        report.problem("Zillow", _error_text(error))
    if not props and (errors or data.get("status") == "failed"):
        report.missing("Zillow", f"job {job_id} returned no listings")
        report.raise_if_failed()

    result = screen(props, filters)
    for line in result.traces:
        report.trace(line)
    run = ScreenRun(filters, parsed.to_judge, parsed.unread, result, result.matches)
    if listing_urls:
        run.listings = result.matches + result.rejected
    elif not include_evaluated:
        archive = load_archive(report)
        run.matches = []
        for row in result.matches:
            source = archive.find(row)
            if source:
                report.trace(f"{row['address']}: already evaluated ({source})")
            else:
                run.matches.append(row)
        run.already_evaluated = len(result.matches) - len(run.matches)
    return run


def search_urls(criteria: Criteria) -> List[str]:
    """One Zillow search URL per target zip code or city, capped at the criteria's max price."""
    max_price = int(criteria.max_price) if criteria.max_price else None
    areas = criteria.search_areas()
    if criteria.zipcodes:
        return _build_search_urls(zipcode=areas, max_price=max_price)
    return [url for area in areas for url in _build_search_urls(location=area, max_price=max_price)]


def _headers() -> Dict[str, str]:
    api_key = Config().rapidapi_key
    if not api_key:
        raise ValueError("RAPIDAPI_KEY is not set; configure the RapidAPI key for Zillow Property Data")
    return {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _submit(headers: Dict[str, str], body: Dict[str, Any]) -> str:
    resp = requests.post(f"{BASE_URL}/properties", headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("job_id"):
        raise RuntimeError(f"Zillow returned no job_id: {json.dumps(data)[:300]}")
    return data["job_id"]


def _read(headers: Dict[str, str], job_id: str) -> Dict[str, Any]:
    resp = requests.get(f"{BASE_URL}/results/{job_id}", headers=headers, timeout=15)
    if resp.status_code == 404:
        raise ValueError(f"Zillow job {job_id} not found or expired; run property_screen again without job_id")
    resp.raise_for_status()
    return resp.json()


def _wait(headers: Dict[str, str], job_id: str, polling: Polling) -> Tuple[Dict[str, Any], int]:
    """The finished job's data, and the result reads it took."""
    time.sleep(polling.first_wait)
    for read in range(1, polling.max_reads + 1):
        data = _read(headers, job_id)
        if data.get("status") in DONE:
            return data, read
        if read < polling.max_reads:
            time.sleep(polling.interval)
    raise JobPending(job_id, polling.max_reads)


def _error_text(error: Any) -> str:
    """One of Zillow's per-URL errors in a line: "<url>: <error>"."""
    if isinstance(error, dict) and error.get("error"):
        return f"{error['url']}: {error['error']}" if error.get("url") else str(error["error"])
    return json.dumps(error, ensure_ascii=False, default=str)[:300]


def _as_list(urls: Optional[Union[List[str], str]]) -> List[str]:
    if not urls:
        return []
    items = urls if isinstance(urls, list) else str(urls).split(",")
    return [str(u).strip() for u in items if str(u).strip()]
