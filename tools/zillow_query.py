import json
import os
import time
import re
from typing import Any, Dict, List, Optional, Union
from langchain_core.tools import tool
import requests

from core.util import format_tool_response
from core.util.config import Config


BASE_URL = "https://zillow-property-data1.p.rapidapi.com/v1"
RAPIDAPI_HOST = "zillow-property-data1.p.rapidapi.com"


@tool
def zillow_query(
    action: str = "search",
    zipcode: Optional[Union[str, int, List[Union[str, int]]]] = None,
    location: str = "",
    city: str = "",
    state: str = "",
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[float] = None,
    search_url: str = "",
    urls: Optional[Union[List[str], str]] = None,
    zpid: Optional[Union[int, str]] = None,
    zpids: Optional[Union[List[Union[int, str]], str]] = None,
    job_id: str = "",
    wait_for_results: bool = True,
    timeout_seconds: int = 30,
    poll_interval: float = 2.0,
    agent_id: str = "",
) -> str:
    """
    Search and inspect active real estate listings, property data, rent estimates, and valuations
    using the Zillow Property Data API via RapidAPI.

    Supported Actions:
    - 'search' (default): Searches active listings by zip code, city/state, location, or search URL.
        Args: zipcode (e.g. '98109' or ['98109', '98102']), location (e.g. 'Seattle, WA'),
        city, state, min_price, max_price, bedrooms, bathrooms, search_url, urls.
        Automatically submits the query, polls for completed results, and returns enriched property data
        with calculated Price/SqFt and Cap Rate estimates.
    - 'details' (or 'property', 'lookup'): Retrieves full property details for specific ZPID(s) or listing URL(s).
        Args: zpid, zpids (e.g. [441587555, 48953419]), url, urls.
    - 'results' (or 'job_results', 'status'): Fetches the status and results for a previous asynchronous job_id.
        Args: job_id (required).
    - 'submit_job': Submits an async search or property batch job and immediately returns the job_id without polling.
        Args: urls, zpids, or search criteria.

    Args:
        action: The action to execute ('search', 'details', 'results', 'submit_job'). Defaults to 'search'.
        zipcode: Target 5-digit zip code (str/int) or list of zip codes (e.g. '98109', '98109, 98102', ['98109', '98102']).
        location: City and state string (e.g. 'Seattle, WA', 'Austin, TX').
        city: City name (e.g. 'Seattle').
        state: 2-letter state code (e.g. 'WA').
        min_price: Minimum price filter (e.g. 500000).
        max_price: Maximum price filter (e.g. 1200000).
        bedrooms: Minimum bedrooms filter.
        bathrooms: Minimum bathrooms filter.
        search_url: Direct custom Zillow search URL (e.g. 'https://www.zillow.com/homes/for_sale/98109_rb/').
        urls: List or comma-separated string of Zillow URLs to scrape/lookup.
        zpid: Single Zillow Property ID (e.g. 441587555 or '441587555').
        zpids: List or comma-separated string of Zillow Property IDs.
        job_id: Job ID string from a previous async request (required for 'results' action).
        wait_for_results: When True (default), automatically polls until job completes and returns data.
        timeout_seconds: Maximum seconds to wait when polling for results (default: 30).
        poll_interval: Seconds between poll attempts (default: 2.0).
        agent_id: Optional agent ID for permission checking.

    Returns:
        Structured XML-wrapped JSON response with properties, metrics, and any errors.
    """
    # Permission verification if agent_id is passed
    if agent_id:
        from core.loaders.tools_loader import ToolsLoader
        tools_loader = ToolsLoader()
        if not tools_loader.check_permission(agent_id, "zillow_query", action):
            return format_tool_response(
                "zillow_query",
                payload="",
                errors=f"Error: Agent '{agent_id}' does not have permission to execute action '{action}' on zillow_query."
            )

    api_key = Config().rapidapi_key
    if not api_key:
        return format_tool_response(
            "zillow_query",
            payload="",
            errors="Error: RAPIDAPI_KEY environment variable not set. Please configure your RapidAPI key for Zillow Property Data."
        )

    headers = {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    norm_action = action.lower().strip()

    try:
        if norm_action in ("search", "query", "find_properties", "search_listings"):
            return _handle_search(
                headers=headers,
                zipcode=zipcode,
                location=location,
                city=city,
                state=state,
                min_price=min_price,
                max_price=max_price,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                search_url=search_url,
                urls=urls,
                wait_for_results=wait_for_results,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

        elif norm_action in ("details", "property", "get_property", "lookup"):
            return _handle_details(
                headers=headers,
                zpid=zpid,
                zpids=zpids,
                url=search_url,
                urls=urls,
                wait_for_results=wait_for_results,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

        elif norm_action in ("results", "job_results", "status", "get_results"):
            return _handle_get_results(
                headers=headers,
                job_id=job_id,
            )

        elif norm_action in ("submit_job", "async_search"):
            return _handle_submit_job(
                headers=headers,
                zipcode=zipcode,
                location=location,
                city=city,
                state=state,
                min_price=min_price,
                max_price=max_price,
                search_url=search_url,
                urls=urls,
                zpid=zpid,
                zpids=zpids,
            )

        else:
            return format_tool_response(
                "zillow_query",
                payload="",
                errors=f"Error: Unknown action '{action}'. Supported actions: 'search', 'details', 'results', 'submit_job'."
            )

    except requests.RequestException as e:
        error_msg = f"Network or HTTP error communicating with Zillow Property Data API: {e}"
        if hasattr(e, "response") and e.response is not None:
            try:
                err_json = e.response.json()
                error_msg += f" Response: {json.dumps(err_json)}"
            except Exception:
                error_msg += f" Status: {e.response.status_code} Body: {e.response.text[:300]}"
        return format_tool_response("zillow_query", payload="", errors=error_msg)
    except Exception as e:
        return format_tool_response("zillow_query", payload="", errors=f"Error in zillow_query: {e}")


def _build_search_urls(
    zipcode: Optional[Union[str, int, List[Union[str, int]]]] = None,
    location: str = "",
    city: str = "",
    state: str = "",
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    search_url: str = "",
    urls: Optional[Union[List[str], str]] = None,
) -> List[str]:
    """Constructs valid Zillow search/listing URLs based on provided search parameters."""
    built_urls: List[str] = []

    # 1. Custom URL(s) provided directly
    if search_url and search_url.strip():
        built_urls.append(search_url.strip())

    if urls:
        if isinstance(urls, list):
            built_urls.extend([str(u).strip() for u in urls if str(u).strip()])
        elif isinstance(urls, str):
            for u in urls.split(","):
                if u.strip():
                    built_urls.append(u.strip())

    if built_urls:
        return built_urls

    # Price filter string if applicable
    price_suffix = ""
    if min_price is not None or max_price is not None:
        min_p = str(int(min_price)) if min_price is not None else "0"
        max_p = str(int(max_price)) if max_price is not None else ""
        price_suffix = f"{min_p}-{max_p}_price/"

    # 2. Zipcode(s) provided
    parsed_zipcodes: List[str] = []
    if zipcode is not None:
        if isinstance(zipcode, (list, tuple, set)):
            for z in zipcode:
                if str(z).strip():
                    parsed_zipcodes.append(str(z).strip())
        elif isinstance(zipcode, (int, str)):
            for z in str(zipcode).split(","):
                if z.strip():
                    parsed_zipcodes.append(z.strip())

    for z in parsed_zipcodes:
        # Normalize 5 digit zip
        match = re.search(r"\b\d{5}\b", z)
        clean_zip = match.group(0) if match else z
        url = f"https://www.zillow.com/homes/for_sale/{clean_zip}_rb/{price_suffix}"
        built_urls.append(url)

    if built_urls:
        return built_urls

    # 3. Location / City / State provided
    target_location = location.strip()
    if not target_location and city.strip():
        target_location = f"{city.strip()}, {state.strip()}".rstrip(", ")

    if target_location:
        # Transform "Seattle, WA" -> "Seattle-WA"
        slug = re.sub(r"[\s,]+", "-", target_location.strip()).strip("-")
        url = f"https://www.zillow.com/homes/for_sale/{slug}/{price_suffix}"
        built_urls.append(url)

    return built_urls


def _parse_zpids(
    zpid: Optional[Union[int, str]] = None,
    zpids: Optional[Union[List[Union[int, str]], str]] = None,
) -> List[int]:
    """Parses single or multiple ZPIDs into a list of integers."""
    result: List[int] = []
    if zpid is not None and str(zpid).strip():
        try:
            result.append(int(str(zpid).strip()))
        except ValueError:
            pass

    if zpids is not None:
        if isinstance(zpids, (list, tuple, set)):
            for item in zpids:
                try:
                    result.append(int(str(item).strip()))
                except ValueError:
                    pass
        elif isinstance(zpids, str):
            for item in zpids.split(","):
                try:
                    if item.strip():
                        result.append(int(item.strip()))
                except ValueError:
                    pass
    return list(dict.fromkeys(result))


def _enrich_property(prop: Dict[str, Any]) -> Dict[str, Any]:
    """Computes investment and analytical metrics (Price/SqFt, Cap Rate, Gross Yield) on a property object."""
    if not isinstance(prop, dict):
        return prop

    enriched = dict(prop)
    price = prop.get("price")
    living_area = prop.get("living_area")
    rent_zestimate = prop.get("rent_zestimate")
    hoa_fee = prop.get("hoa_fee")

    # Safe numeric casts
    price_num = float(price) if price is not None and isinstance(price, (int, float)) and price > 0 else None
    area_num = float(living_area) if living_area is not None and isinstance(living_area, (int, float)) and living_area > 0 else None
    rent_num = float(rent_zestimate) if rent_zestimate is not None and isinstance(rent_zestimate, (int, float)) and rent_zestimate > 0 else None
    hoa_num = float(hoa_fee) if hoa_fee is not None and isinstance(hoa_fee, (int, float)) and hoa_fee >= 0 else 0.0

    # 1. Price / SqFt
    if price_num and area_num:
        enriched["price_per_sqft"] = round(price_num / area_num, 2)
    else:
        enriched["price_per_sqft"] = None

    # 2. Estimated Cap Rate & Gross Yield
    if price_num and rent_num:
        annual_gross_rent = rent_num * 12.0
        annual_hoa = hoa_num * 12.0
        # Estimated Net Operating Income (accounting for known annual HOA costs)
        estimated_noi = annual_gross_rent - annual_hoa
        enriched["estimated_annual_gross_rent"] = round(annual_gross_rent, 2)
        enriched["estimated_cap_rate_percent"] = round((estimated_noi / price_num) * 100.0, 2)
        enriched["estimated_gross_yield_percent"] = round((annual_gross_rent / price_num) * 100.0, 2)
        enriched["estimated_monthly_gross_yield_percent"] = round((rent_num / price_num) * 100.0, 3)
    else:
        enriched["estimated_annual_gross_rent"] = None
        enriched["estimated_cap_rate_percent"] = None
        enriched["estimated_gross_yield_percent"] = None
        enriched["estimated_monthly_gross_yield_percent"] = None

    return enriched


def _poll_job_results(
    headers: Dict[str, str],
    job_id: str,
    timeout_seconds: int = 30,
    poll_interval: float = 2.0,
) -> Dict[str, Any]:
    """Polls the RapidAPI job results endpoint until complete or timed out."""
    results_url = f"{BASE_URL}/results/{job_id}"
    start_time = time.time()

    last_data: Dict[str, Any] = {}
    while time.time() - start_time < timeout_seconds:
        resp = requests.get(results_url, headers=headers, timeout=15)
        if resp.status_code == 404:
            return {
                "job_id": job_id,
                "status": "not_found",
                "results": [],
                "errors": [{"error": f"Job {job_id} not found or expired on server."}]
            }
        resp.raise_for_status()
        data = resp.json()
        last_data = data

        status = data.get("status", "")
        if status in ("complete", "failed", "finished"):
            return data

        time.sleep(poll_interval)

    # If timeout reached before completion
    if not last_data:
        last_data = {"job_id": job_id, "status": "processing", "results": [], "errors": []}
    last_data["timed_out"] = True
    last_data["note"] = f"Job is still processing after {timeout_seconds}s. Retrieve results later with action='results', job_id='{job_id}'."
    return last_data


def _format_completed_response(raw_data: Dict[str, Any], search_criteria: Optional[Dict[str, Any]] = None) -> str:
    """Formats raw API output with enriched properties, summaries, and error logs."""
    raw_results = raw_data.get("results", [])
    raw_errors = raw_data.get("errors", [])
    job_id = raw_data.get("job_id", "")
    status = raw_data.get("status", "complete")

    properties_list: List[Dict[str, Any]] = []
    for item in raw_results:
        if isinstance(item, dict):
            prop = item.get("property")
            if prop and isinstance(prop, dict):
                enriched = _enrich_property(prop)
                properties_list.append(enriched)

    payload_data: Dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "total_properties": len(properties_list),
        "properties": properties_list,
    }

    if search_criteria:
        payload_data["search_criteria"] = search_criteria

    if raw_data.get("timed_out"):
        payload_data["timed_out"] = True
        payload_data["note"] = raw_data.get("note")

    errors_str = "None"
    if raw_errors:
        payload_data["errors"] = raw_errors
        errors_str = json.dumps(raw_errors)

    return format_tool_response(
        "zillow_query",
        payload=json.dumps(payload_data, indent=2),
        errors=errors_str
    )


def _handle_search(
    headers: Dict[str, str],
    zipcode: Optional[Union[str, int, List[Union[str, int]]]],
    location: str,
    city: str,
    state: str,
    min_price: Optional[int],
    max_price: Optional[int],
    bedrooms: Optional[int],
    bathrooms: Optional[float],
    search_url: str,
    urls: Optional[Union[List[str], str]],
    wait_for_results: bool,
    timeout_seconds: int,
    poll_interval: float,
) -> str:
    target_urls = _build_search_urls(
        zipcode=zipcode,
        location=location,
        city=city,
        state=state,
        min_price=min_price,
        max_price=max_price,
        search_url=search_url,
        urls=urls,
    )

    if not target_urls:
        return format_tool_response(
            "zillow_query",
            payload="",
            errors="Error: No valid search target provided. Specify at least one of 'zipcode', 'location', 'city'/'state', or 'search_url'."
        )

    submit_url = f"{BASE_URL}/properties"
    payload = {"urls": target_urls}

    resp = requests.post(submit_url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    submit_data = resp.json()

    job_id = submit_data.get("job_id")
    if not job_id:
        return format_tool_response(
            "zillow_query",
            payload=json.dumps(submit_data, indent=2),
            errors="Error: RapidAPI did not return a job_id."
        )

    search_criteria = {
        "zipcode": zipcode,
        "location": location or (f"{city}, {state}".strip(", ") if city else None),
        "min_price": min_price,
        "max_price": max_price,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "generated_urls": target_urls,
    }

    if not wait_for_results:
        async_resp = {
            "job_id": job_id,
            "status": "processing",
            "search_criteria": search_criteria,
            "message": f"Job submitted successfully. Retrieve results with action='results', job_id='{job_id}'."
        }
        return format_tool_response("zillow_query", payload=json.dumps(async_resp, indent=2), errors="None")

    completed_data = _poll_job_results(
        headers=headers,
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )

    return _format_completed_response(completed_data, search_criteria=search_criteria)


def _handle_details(
    headers: Dict[str, str],
    zpid: Optional[Union[int, str]],
    zpids: Optional[Union[List[Union[int, str]], str]],
    url: str,
    urls: Optional[Union[List[str], str]],
    wait_for_results: bool,
    timeout_seconds: int,
    poll_interval: float,
) -> str:
    target_zpids = _parse_zpids(zpid=zpid, zpids=zpids)
    target_urls = _build_search_urls(search_url=url, urls=urls)

    if not target_zpids and not target_urls:
        return format_tool_response(
            "zillow_query",
            payload="",
            errors="Error: Provide at least one 'zpid', 'zpids', 'url', or 'urls' for property details lookup."
        )

    payload: Dict[str, Any] = {}
    if target_zpids:
        payload["zpids"] = target_zpids
    if target_urls:
        payload["urls"] = target_urls

    submit_url = f"{BASE_URL}/properties"
    resp = requests.post(submit_url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    submit_data = resp.json()

    job_id = submit_data.get("job_id")
    if not job_id:
        return format_tool_response(
            "zillow_query",
            payload=json.dumps(submit_data, indent=2),
            errors="Error: RapidAPI did not return a job_id."
        )

    if not wait_for_results:
        async_resp = {
            "job_id": job_id,
            "status": "processing",
            "lookup": payload,
            "message": f"Lookup job submitted successfully. Retrieve results with action='results', job_id='{job_id}'."
        }
        return format_tool_response("zillow_query", payload=json.dumps(async_resp, indent=2), errors="None")

    completed_data = _poll_job_results(
        headers=headers,
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )

    return _format_completed_response(completed_data, search_criteria=payload)


def _handle_get_results(headers: Dict[str, str], job_id: str) -> str:
    if not job_id or not job_id.strip():
        return format_tool_response(
            "zillow_query",
            payload="",
            errors="Error: 'job_id' is required for action='results'."
        )

    results_url = f"{BASE_URL}/results/{job_id.strip()}"
    resp = requests.get(results_url, headers=headers, timeout=15)
    if resp.status_code == 404:
        return format_tool_response(
            "zillow_query",
            payload="",
            errors=f"Error: Job ID '{job_id}' not found or expired on Zillow Property Data API."
        )
    resp.raise_for_status()
    data = resp.json()

    return _format_completed_response(data)


def _handle_submit_job(
    headers: Dict[str, str],
    zipcode: Optional[Union[str, int, List[Union[str, int]]]] = None,
    location: str = "",
    city: str = "",
    state: str = "",
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    search_url: str = "",
    urls: Optional[Union[List[str], str]] = None,
    zpid: Optional[Union[int, str]] = None,
    zpids: Optional[Union[List[Union[int, str]], str]] = None,
) -> str:
    target_zpids = _parse_zpids(zpid=zpid, zpids=zpids)
    target_urls = _build_search_urls(
        zipcode=zipcode,
        location=location,
        city=city,
        state=state,
        min_price=min_price,
        max_price=max_price,
        search_url=search_url,
        urls=urls,
    )

    if not target_zpids and not target_urls:
        return format_tool_response(
            "zillow_query",
            payload="",
            errors="Error: Provide at least one search criterion, URL, or ZPID to submit a job."
        )

    payload: Dict[str, Any] = {}
    if target_zpids:
        payload["zpids"] = target_zpids
    if target_urls:
        payload["urls"] = target_urls

    submit_url = f"{BASE_URL}/properties"
    resp = requests.post(submit_url, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    return format_tool_response("zillow_query", payload=json.dumps(data, indent=2), errors="None")
