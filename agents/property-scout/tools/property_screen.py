"""property_screen: the new listings in the user's target area that pass criteria.md, in one call."""
import json
from typing import Any, Dict, List, Optional, Union

import requests

from core.tooling import Report, ToolError, agent_tool
from agents.property_scout.lib.scout import JobPending, run_screen


@agent_tool
def property_screen(
    report: Report,
    criteria: Optional[Dict[str, Any]] = None,
    urls: Optional[Union[List[str], str]] = None,
    job_id: str = "",
    include_evaluated: bool = False,
) -> dict:
    """Screen active Zillow listings against the user's criteria (pkm/wallet/real-estate/criteria.md) in one call.

    Reads: criteria.md, the archive (properties/ reports, passed.md), and one Zillow job for every target city or zip code (20-60 s).
    Computes: the hard filters each listing fails; $/sqft vs. the median of the listings pulled in its city; the cap rate (rent net of HOA) and yields; which matches are new.
    Leaves out: the TOUR or PASS verdict, and criteria.md's other lines (dealbreakers, condition), returned as written in criteria_to_judge.

    Returns JSON: criteria (the filters applied), pulled (listings checked), matches (preferred layout first, then
    lowest $/sqft), already_evaluated, failed_by_rule (listings each filter rejected), near_misses (up to 5 that
    failed one filter) and criteria_to_judge. Money reads "USD 849,000"; hoa_fee and rent_zestimate are monthly;
    hoa_fee null means none listed; the cap rate and yields are null without a rent estimate. With `urls`, every
    listing comes back under "listings", each with `missed`: the filters it failed (empty: it passes).

    Args:
        criteria: Overrides of the file's filters for this call only, e.g. {"cities": ["Brockway"], "min_beds": 4}.
            Keys: cities, zipcodes, state, max_price, min_beds, min_baths, min_sqft, max_hoa, home_types,
            max_stories, min_cap_rate; null drops a filter. Omit to use the file as written.
        urls: Zillow listing URLs to check instead of searching, e.g. a home the user sent.
        job_id: From a reply saying Zillow was still processing: reads that job's results without a new search.
        include_evaluated: Also return matches already evaluated. Defaults to False.
    """
    try:
        return run_screen(report, criteria, urls, job_id, include_evaluated).payload()
    except JobPending as e:
        raise ToolError(
            f"Zillow is still processing job {e.job_id}. Call property_screen(job_id=\"{e.job_id}\") "
            "in a minute to read its results without a new search."
        ) from e
    except requests.RequestException as e:
        raise ToolError(_http_error(e)) from e
    except ValueError as e:
        raise ToolError(str(e)) from e


def _http_error(e: requests.RequestException) -> str:
    message = f"Network or HTTP error from the Zillow Property Data API: {e}"
    response = getattr(e, "response", None)
    if response is not None:
        try:
            message += f" Response: {json.dumps(response.json())}"
        except Exception:
            message += f" Status: {response.status_code} Body: {response.text[:300]}"
    return message
