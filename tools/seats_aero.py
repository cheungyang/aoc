import json
import os
from typing import Any, List, Optional, Union
from langchain_core.tools import tool
import requests

from core.util import format_tool_response
from core.util.config import Config


BASE_URL = "https://seats.aero/partnerapi"


@tool
def seats_aero(
    action: str = "search",
    origin_airport: str = "",
    destination_airport: str = "",
    start_date: str = "",
    end_date: str = "",
    cabins: str = "",
    sources: str = "",
    carriers: str = "",
    only_direct_flights: bool = False,
    order_by: str = "",
    take: int = 50,
    skip: int = 0,
    cursor: Optional[int] = None,
    availability_id: str = "",
    availability_ids: Optional[Union[List[str], str]] = None,
    source: str = "",
    cabin: str = "",
    origin_region: str = "",
    destination_region: str = "",
    departure_date: str = "",
    seat_count: int = 1,
    include_trips: bool = False,
    include_filtered: bool = False,
    disable_filters: bool = False,
    show_dynamic_pricing: bool = False,
    agent_id: str = "",
) -> str:
    """
    Search and inspect award flight availability across 20+ mileage programs using the Seats.aero partner API.

    Supported Actions:
    - 'search' (or 'cached_search'): Searches cached award availability between airport pairs and date ranges.
        Args: origin_airport, destination_airport, start_date, end_date (YYYY-MM-DD).
        Optional args: cabins ('economy,premium,business,first'), sources ('aeroplan,united,american,flyingblue,...'),
        carriers ('NH,JL,BA,...'), only_direct_flights (bool), order_by ('lowest_mileage'), take, skip, cursor,
        include_trips (bool), include_filtered (bool).
    - 'trips' (or 'get_trips'): Retrieves flight-level itinerary details (flight numbers, duration, stops, exact taxes, aircraft, booking links) from an Availability object ID.
        Args: availability_id (required), include_filtered (bool).
    - 'destinations' (or 'get_destinations'): Discovers nonstop routes reachable from (or to) an airport and lowest points cost per cabin across all sources.
        Args: exactly one of origin_airport OR destination_airport.
    - 'bulk_availability' (or 'explore'): Retrieves high-volume regional availability for one specific mileage program.
        Args: source (e.g. 'aeroplan', required). Optional: cabin, origin_region, destination_region, start_date, end_date, take, skip, cursor.
    - 'routes' (or 'get_routes'): Lists all monitored routes for a specific mileage program.
        Args: source (e.g. 'aeroplan', required).
    - 'refresh' (or 'refresh_cache'): Requests an async refresh for stale cached availability objects (Pro API key).
        Args: availability_ids (list or comma-separated string of IDs).
    - 'live_search': Executes real-time live availability query for any city pair and date (commercial key only).
        Args: origin_airport, destination_airport, departure_date, source, seat_count, disable_filters, show_dynamic_pricing.

    Args:
        action: The API action to execute. Defaults to 'search'.
        origin_airport: 3-letter IATA origin airport code (e.g. 'SFO' or 'SFO,OAK,SJC').
        destination_airport: 3-letter IATA destination airport code (e.g. 'NRT,HND' or 'LHR').
        start_date: Earliest departure date in YYYY-MM-DD format.
        end_date: Latest departure date in YYYY-MM-DD format.
        cabins: Comma-separated cabins to filter ('economy', 'premium', 'business', 'first').
        sources: Comma-separated mileage programs to search (e.g. 'aeroplan,united,american,flyingblue,virginatlantic,alaska,delta,singapore,emirates,qantas,lifemiles').
        carriers: Comma-separated operating/marketing airline codes (e.g. 'NH,SQ,BA,AF,UA').
        only_direct_flights: When True, returns only nonstop flights.
        order_by: Set to 'lowest_mileage' to sort by cheapest award price first.
        take: Number of results to return (default 50, max 1000).
        skip: Number of results to skip for pagination.
        cursor: Pagination cursor from a prior search response.
        availability_id: Availability object ID (required for 'trips' action).
        availability_ids: List of availability IDs (required for 'refresh' action).
        source: Single mileage program source name (required for 'bulk_availability', 'routes', 'live_search').
        cabin: Single cabin name for bulk search ('economy', 'premium', 'business', 'first').
        origin_region: Filter for bulk search ('North America', 'Europe', 'Asia', 'Oceania', 'South America', 'Africa').
        destination_region: Filter for bulk search ('North America', 'Europe', 'Asia', 'Oceania', 'South America', 'Africa').
        departure_date: Departure date for live_search (YYYY-MM-DD).
        seat_count: Number of passenger seats to search for (1-9).
        include_trips: Include granular flight segment info inline in cached search results.
        include_filtered: Include dynamically-priced/raw results.
        disable_filters: Disable dynamic price filtering in live search.
        show_dynamic_pricing: Show dynamic pricing in live search.
        agent_id: Optional agent ID for permission checking.
    """
    # Permission verification if agent_id is passed
    if agent_id:
        from core.loaders.tools_loader import ToolsLoader
        tools_loader = ToolsLoader()
        if not tools_loader.check_permission(agent_id, "seats_aero", action):
            return format_tool_response(
                "seats_aero",
                payload="",
                errors=f"Error: Agent '{agent_id}' does not have permission to execute action '{action}' on seats_aero."
            )

    api_key = Config().seats_aero_api_key
    if not api_key:
        return format_tool_response(
            "seats_aero",
            payload="",
            errors="Error: SEATS_AERO_API_KEY environment variable not set. Please set your Seats.aero partner API key."
        )

    headers = {
        "Partner-Authorization": api_key,
        "Accept": "application/json",
    }

    norm_action = action.lower().strip()

    try:
        if norm_action in ("search", "cached_search"):
            return _handle_cached_search(
                headers=headers,
                origin_airport=origin_airport,
                destination_airport=destination_airport,
                start_date=start_date,
                end_date=end_date,
                cabins=cabins,
                sources=sources or source,
                carriers=carriers,
                only_direct_flights=only_direct_flights,
                order_by=order_by,
                take=take,
                skip=skip,
                cursor=cursor,
                include_trips=include_trips,
                include_filtered=include_filtered,
            )

        elif norm_action in ("trips", "get_trips"):
            return _handle_get_trips(
                headers=headers,
                availability_id=availability_id,
                include_filtered=include_filtered,
            )

        elif norm_action in ("destinations", "get_destinations"):
            return _handle_get_destinations(
                headers=headers,
                origin_airport=origin_airport,
                destination_airport=destination_airport,
            )

        elif norm_action in ("bulk_availability", "explore", "availability"):
            return _handle_bulk_availability(
                headers=headers,
                source=source or sources,
                cabin=cabin or cabins,
                origin_region=origin_region,
                destination_region=destination_region,
                start_date=start_date,
                end_date=end_date,
                take=take,
                skip=skip,
                cursor=cursor,
                include_filtered=include_filtered,
            )

        elif norm_action in ("routes", "get_routes"):
            return _handle_get_routes(
                headers=headers,
                source=source or sources,
            )

        elif norm_action in ("refresh", "refresh_cache"):
            return _handle_refresh(
                headers=headers,
                availability_ids=availability_ids,
                availability_id=availability_id,
            )

        elif norm_action in ("live_search", "live"):
            return _handle_live_search(
                headers=headers,
                origin_airport=origin_airport,
                destination_airport=destination_airport,
                departure_date=departure_date or start_date,
                source=source or sources,
                seat_count=seat_count,
                disable_filters=disable_filters,
                show_dynamic_pricing=show_dynamic_pricing,
            )

        else:
            return format_tool_response(
                "seats_aero",
                payload="",
                errors=f"Error: Unknown action '{action}'. Supported actions: 'search', 'trips', 'destinations', 'bulk_availability', 'routes', 'refresh', 'live_search'."
            )

    except requests.RequestException as e:
        error_msg = f"Network or HTTP error communicating with Seats.aero: {e}"
        if hasattr(e, "response") and e.response is not None:
            try:
                err_json = e.response.json()
                error_msg += f" Response: {json.dumps(err_json)}"
            except Exception:
                error_msg += f" Status: {e.response.status_code} Body: {e.response.text[:300]}"
        return format_tool_response("seats_aero", payload="", errors=error_msg)
    except Exception as e:
        return format_tool_response("seats_aero", payload="", errors=f"Error in seats_aero: {e}")


def _handle_cached_search(
    headers: dict,
    origin_airport: str,
    destination_airport: str,
    start_date: str,
    end_date: str,
    cabins: str,
    sources: str,
    carriers: str,
    only_direct_flights: bool,
    order_by: str,
    take: int,
    skip: int,
    cursor: Optional[int],
    include_trips: bool,
    include_filtered: bool,
) -> str:
    if not origin_airport:
        return format_tool_response("seats_aero", payload="", errors="Error: 'origin_airport' is required for search action.")
    if not destination_airport:
        return format_tool_response("seats_aero", payload="", errors="Error: 'destination_airport' is required for search action.")

    params = {
        "origin_airport": origin_airport.strip().upper(),
        "destination_airport": destination_airport.strip().upper(),
        "take": min(max(take, 10), 1000),
        "skip": skip,
    }

    if start_date:
        params["start_date"] = start_date.strip()
    if end_date:
        params["end_date"] = end_date.strip()
    if cabins:
        params["cabins"] = cabins.strip().lower()
    if sources:
        params["sources"] = sources.strip().lower()
    if carriers:
        params["carriers"] = carriers.strip().upper()
    if only_direct_flights:
        params["only_direct_flights"] = "true"
    if order_by:
        params["order_by"] = order_by.strip().lower()
    if cursor is not None:
        params["cursor"] = cursor
    if include_trips:
        params["include_trips"] = "true"
        params["minify_trips"] = "true"
    if include_filtered:
        params["include_filtered"] = "true"

    url = f"{BASE_URL}/search"
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    raw_data = resp.json()

    results_list = raw_data.get("data", [])
    has_more = raw_data.get("hasMore", False)
    next_cursor = raw_data.get("cursor")

    summarized_items = []
    for item in results_list:
        route = item.get("Route", {})
        summary = {
            "availability_id": item.get("ID"),
            "route": f"{route.get('OriginAirport', '')} -> {route.get('DestinationAirport', '')}",
            "distance_miles": route.get("Distance"),
            "source_program": item.get("Source"),
            "date": item.get("Date"),
            "cabins": {},
        }

        # Cabin details
        if item.get("YAvailable"):
            summary["cabins"]["economy"] = {
                "mileage_cost": int(item.get("YMileageCost", 0)) if str(item.get("YMileageCost", "0")).isdigit() else item.get("YMileageCost"),
                "remaining_seats": item.get("YRemainingSeats", 0),
                "airlines": item.get("YAirlines", ""),
                "direct": item.get("YDirect", False),
            }
        if item.get("WAvailable"):
            summary["cabins"]["premium"] = {
                "mileage_cost": int(item.get("WMileageCost", 0)) if str(item.get("WMileageCost", "0")).isdigit() else item.get("WMileageCost"),
                "remaining_seats": item.get("WRemainingSeats", 0),
                "airlines": item.get("WAirlines", ""),
                "direct": item.get("WDirect", False),
            }
        if item.get("JAvailable"):
            summary["cabins"]["business"] = {
                "mileage_cost": int(item.get("JMileageCost", 0)) if str(item.get("JMileageCost", "0")).isdigit() else item.get("JMileageCost"),
                "remaining_seats": item.get("JRemainingSeats", 0),
                "airlines": item.get("JAirlines", ""),
                "direct": item.get("JDirect", False),
            }
        if item.get("FAvailable"):
            summary["cabins"]["first"] = {
                "mileage_cost": int(item.get("FMileageCost", 0)) if str(item.get("FMileageCost", "0")).isdigit() else item.get("FMileageCost"),
                "remaining_seats": item.get("FRemainingSeats", 0),
                "airlines": item.get("FAirlines", ""),
                "direct": item.get("FDirect", False),
            }

        # Inline trip details if requested
        if include_trips and item.get("AvailabilityTrips"):
            summary["trips"] = _format_trips_list(item.get("AvailabilityTrips"))

        summarized_items.append(summary)

    payload_data = {
        "count": len(summarized_items),
        "has_more": has_more,
        "cursor": next_cursor,
        "search_parameters": {
            "origin": origin_airport,
            "destination": destination_airport,
            "start_date": start_date,
            "end_date": end_date,
            "cabins": cabins,
            "sources": sources,
        },
        "results": summarized_items,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _handle_get_trips(headers: dict, availability_id: str, include_filtered: bool) -> str:
    if not availability_id:
        return format_tool_response("seats_aero", payload="", errors="Error: 'availability_id' is required for trips action.")

    url = f"{BASE_URL}/trips/{availability_id.strip()}"
    params = {}
    if include_filtered:
        params["include_filtered"] = "true"

    resp = requests.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code == 404:
        return format_tool_response("seats_aero", payload="", errors=f"Error: Availability object '{availability_id}' not found or has expired.")
    resp.raise_for_status()

    raw_data = resp.json()
    trips_raw = raw_data.get("data", [])
    booking_links = raw_data.get("booking_links", [])

    formatted_trips = _format_trips_list(trips_raw)

    payload_data = {
        "availability_id": availability_id,
        "trip_count": len(formatted_trips),
        "trips": formatted_trips,
        "booking_links": booking_links,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _format_trips_list(trips_raw: list) -> list:
    formatted = []
    for trip in trips_raw:
        segments_raw = trip.get("AvailabilitySegments") or []
        segments = []
        for seg in segments_raw:
            segments.append({
                "flight_number": seg.get("FlightNumber"),
                "carrier": seg.get("FlightNumber")[:2] if seg.get("FlightNumber") else "",
                "from": seg.get("OriginAirport"),
                "to": seg.get("DestinationAirport"),
                "departure_local": seg.get("DepartsAt"),
                "arrival_local": seg.get("ArrivesAt"),
                "aircraft_name": seg.get("AircraftName") or seg.get("AircraftCode"),
                "fare_class": seg.get("FareClass"),
                "distance_miles": seg.get("Distance"),
            })

        duration_mins = trip.get("TotalDuration", 0)
        dur_str = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins else ""

        taxes_raw = trip.get("TotalTaxes", 0)
        taxes_curr = trip.get("TaxesCurrencySymbol") or "$"
        taxes_formatted = f"{taxes_curr}{taxes_raw / 100:.2f}" if taxes_raw else "N/A"

        formatted.append({
            "trip_id": trip.get("ID"),
            "cabin": trip.get("Cabin"),
            "mileage_cost": trip.get("MileageCost"),
            "total_taxes": taxes_formatted,
            "stops": trip.get("Stops"),
            "carriers": trip.get("Carriers"),
            "flight_numbers": trip.get("FlightNumbers"),
            "total_duration": dur_str,
            "departs_at": trip.get("DepartsAt"),
            "arrives_at": trip.get("ArrivesAt"),
            "remaining_seats": trip.get("RemainingSeats"),
            "source_program": trip.get("Source"),
            "segments": segments,
        })
    return formatted


def _handle_get_destinations(headers: dict, origin_airport: str, destination_airport: str) -> str:
    if bool(origin_airport) == bool(destination_airport):
        return format_tool_response(
            "seats_aero",
            payload="",
            errors="Error: Provide exactly ONE of 'origin_airport' or 'destination_airport' for destinations action."
        )

    params = {}
    if origin_airport:
        params["origin_airport"] = origin_airport.strip().upper()
    if destination_airport:
        params["destination_airport"] = destination_airport.strip().upper()

    url = f"{BASE_URL}/destinations"
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()

    raw_data = resp.json()
    destinations = raw_data.get("destinations", [])

    payload_data = {
        "requested_origin": raw_data.get("origin_airport"),
        "requested_destination": raw_data.get("destination_airport"),
        "destination_count": len(destinations),
        "nonstop_destinations": destinations,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _handle_bulk_availability(
    headers: dict,
    source: str,
    cabin: str,
    origin_region: str,
    destination_region: str,
    start_date: str,
    end_date: str,
    take: int,
    skip: int,
    cursor: Optional[int],
    include_filtered: bool,
) -> str:
    if not source:
        return format_tool_response(
            "seats_aero",
            payload="",
            errors="Error: 'source' (e.g. 'aeroplan', 'united', 'flyingblue') is required for bulk_availability action."
        )

    params = {
        "source": source.strip().lower(),
        "take": min(max(take, 10), 1000),
        "skip": skip,
    }

    if cabin:
        params["cabin"] = cabin.strip().lower()
    if origin_region:
        params["origin_region"] = origin_region.strip()
    if destination_region:
        params["destination_region"] = destination_region.strip()
    if start_date:
        params["start_date"] = start_date.strip()
    if end_date:
        params["end_date"] = end_date.strip()
    if cursor is not None:
        params["cursor"] = cursor
    if include_filtered:
        params["include_filtered"] = "true"

    url = f"{BASE_URL}/availability"
    resp = requests.get(url, params=params, headers=headers, timeout=25)
    resp.raise_for_status()

    raw_data = resp.json()
    results_list = raw_data.get("data", [])
    has_more = raw_data.get("hasMore", False)
    next_cursor = raw_data.get("cursor")

    summarized_items = []
    for item in results_list:
        route = item.get("Route", {})
        summary = {
            "availability_id": item.get("ID"),
            "route": f"{route.get('OriginAirport', '')} -> {route.get('DestinationAirport', '')}",
            "origin_region": route.get("OriginRegion"),
            "destination_region": route.get("DestinationRegion"),
            "source_program": item.get("Source"),
            "date": item.get("Date"),
            "cabins": {},
        }
        if item.get("YAvailable"):
            summary["cabins"]["economy"] = {
                "mileage_cost": item.get("YMileageCost"),
                "remaining_seats": item.get("YRemainingSeats", 0),
                "direct": item.get("YDirect", False),
            }
        if item.get("WAvailable"):
            summary["cabins"]["premium"] = {
                "mileage_cost": item.get("WMileageCost"),
                "remaining_seats": item.get("WRemainingSeats", 0),
                "direct": item.get("WDirect", False),
            }
        if item.get("JAvailable"):
            summary["cabins"]["business"] = {
                "mileage_cost": item.get("JMileageCost"),
                "remaining_seats": item.get("JRemainingSeats", 0),
                "direct": item.get("JDirect", False),
            }
        if item.get("FAvailable"):
            summary["cabins"]["first"] = {
                "mileage_cost": item.get("FMileageCost"),
                "remaining_seats": item.get("FRemainingSeats", 0),
                "direct": item.get("FDirect", False),
            }
        summarized_items.append(summary)

    payload_data = {
        "source": source,
        "count": len(summarized_items),
        "has_more": has_more,
        "cursor": next_cursor,
        "results": summarized_items,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _handle_get_routes(headers: dict, source: str) -> str:
    if not source:
        return format_tool_response("seats_aero", payload="", errors="Error: 'source' (e.g. 'aeroplan', 'united') is required for routes action.")

    url = f"{BASE_URL}/routes"
    params = {"source": source.strip().lower()}

    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()

    routes = resp.json()
    payload_data = {
        "source": source,
        "route_count": len(routes) if isinstance(routes, list) else 0,
        "routes": routes,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _handle_refresh(headers: dict, availability_ids: Optional[Union[List[str], str]], availability_id: str) -> str:
    ids_to_refresh = []
    if isinstance(availability_ids, list):
        ids_to_refresh.extend([str(i).strip() for i in availability_ids if str(i).strip()])
    elif isinstance(availability_ids, str) and availability_ids.strip():
        ids_to_refresh.extend([i.strip() for i in availability_ids.split(",") if i.strip()])
    elif availability_id:
        ids_to_refresh.append(availability_id.strip())

    if not ids_to_refresh:
        return format_tool_response(
            "seats_aero",
            payload="",
            errors="Error: 'availability_ids' (list of IDs) or 'availability_id' is required for refresh action."
        )

    if len(ids_to_refresh) > 250:
        return format_tool_response(
            "seats_aero",
            payload="",
            errors=f"Error: availability_ids is limited to 250 IDs per request (provided {len(ids_to_refresh)})."
        )

    url = f"{BASE_URL}/refresh"
    body = {"availability_ids": ids_to_refresh}
    resp = requests.post(url, json=body, headers=headers, timeout=20)
    resp.raise_for_status()

    return format_tool_response("seats_aero", payload=json.dumps(resp.json(), indent=2), errors="None")


def _handle_live_search(
    headers: dict,
    origin_airport: str,
    destination_airport: str,
    departure_date: str,
    source: str,
    seat_count: int,
    disable_filters: bool,
    show_dynamic_pricing: bool,
) -> str:
    if not origin_airport:
        return format_tool_response("seats_aero", payload="", errors="Error: 'origin_airport' is required for live_search.")
    if not destination_airport:
        return format_tool_response("seats_aero", payload="", errors="Error: 'destination_airport' is required for live_search.")
    if not departure_date:
        return format_tool_response("seats_aero", payload="", errors="Error: 'departure_date' (YYYY-MM-DD) is required for live_search.")
    if not source:
        return format_tool_response("seats_aero", payload="", errors="Error: 'source' (e.g. 'aeroplan', 'delta', 'united') is required for live_search.")

    body = {
        "origin_airport": origin_airport.strip().upper(),
        "destination_airport": destination_airport.strip().upper(),
        "departure_date": departure_date.strip(),
        "source": source.strip().lower(),
        "seat_count": min(max(seat_count, 1), 9),
        "disable_filters": bool(disable_filters),
        "show_dynamic_pricing": bool(show_dynamic_pricing),
    }

    url = f"{BASE_URL}/live"
    resp = requests.post(url, json=body, headers=headers, timeout=30)
    resp.raise_for_status()

    raw_data = resp.json()
    trips_raw = raw_data.get("results", [])
    formatted_trips = _format_trips_list(trips_raw)

    payload_data = {
        "origin_airport": origin_airport,
        "destination_airport": destination_airport,
        "departure_date": departure_date,
        "source": source,
        "result_count": len(formatted_trips),
        "trips": formatted_trips,
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")
