"""The seats_aero tool, and the seats.aero data behind it.

`cached_search` and `trips` read the partner API into frozen dataclasses: a
SearchPage of Availability (a route, a day and a program, with a
CabinAvailability for each cabin that has seats), and Trip itineraries with
their Segments and BookingLinks. The tool formats them as its text. Code that
reads seats.aero without a model uses them directly: reward-travel's
award_watch scan. A call that fails raises SeatsAeroError, which carries the
HTTP status, so a caller can tell a hiccup (a timeout, a 5xx) from what a
person must fix (a rejected key, a spent quota).
"""
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from langchain_core.tools import tool
import requests

from core.tooling import format_tool_response
from core.runtime.foundation.context import try_context
from core.config.settings import Config


BASE_URL = "https://seats.aero/partnerapi"
# The most results one cached search returns.
MAX_TAKE = 1000
# Each cabin, and the letter seats.aero starts its fields with: JAvailable, JMileageCost, ...
CABINS = (("economy", "Y"), ("premium", "W"), ("business", "J"), ("first", "F"))
NO_KEY = "SEATS_AERO_API_KEY is not set"


class SeatsAeroError(Exception):
    """A seats.aero call that failed.

    `status` is the HTTP status, or None when no response came back (a timeout,
    a dropped connection), the response wasn't JSON, or the key is missing. It's
    raised from the requests error, and `is_network_error`
    (core/scheduler/preconditions/scan.py) follows that chain: a timeout or a
    5xx is a hiccup, a 401 or a 429 needs a person.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CabinAvailability:
    """One cabin of an Availability, when it has seats."""
    cabin: str                # economy, premium, business or first
    miles: Optional[int]      # the award's price per person; None when seats.aero's value isn't a number
    seats: int                # seats left; 0 when seats.aero doesn't say
    direct: bool              # whether a nonstop has seats in this cabin
    airlines: str             # the airlines with seats, as seats.aero lists them: "AC, NH"
    taxes_cents: int = 0      # taxes and fees per person, in cents; 0 when not given
    taxes_currency: str = ""  # the taxes' ISO currency code; "" when not given
    taxes_symbol: str = ""    # the currency's symbol, "$", which alone doesn't name it

    @property
    def taxes(self) -> str:
        """The taxes with their ISO code, as award_value reads them: "56.20 CAD"."""
        return _taxes_label(self.taxes_cents, self.taxes_currency, self.taxes_symbol)


@dataclass(frozen=True)
class Segment:
    """One flight of a Trip."""
    flight_number: str  # "UA990"
    origin: str         # IATA code
    destination: str    # IATA code
    departs_at: str     # local departure time, as seats.aero gives it
    arrives_at: str     # local arrival time, as seats.aero gives it

    @property
    def carrier(self) -> str:
        """The airline: the flight number's first two characters."""
        return self.flight_number[:2]


@dataclass(frozen=True)
class BookingLink:
    """Where to book an availability's trips, from seats.aero."""
    label: str     # "Book via Air Canada Aeroplan"
    link: str      # the URL
    primary: bool  # seats.aero's first choice


@dataclass(frozen=True)
class Trip:
    """One itinerary of an availability: its flights, price and seats."""
    id: str                                      # the trip's id
    cabin: str                                   # economy, premium, business or first
    miles: Optional[int]                         # the price per person; None when not given
    taxes_cents: int                             # taxes and fees per person, in cents; 0 when not given
    taxes_currency: str                          # the taxes' ISO currency code; "" when not given
    taxes_symbol: str                            # the currency's symbol, "$"
    stops: Optional[int]                         # connections; None when not given
    carriers: str                                # the airlines flown: "UA, LH"
    flight_numbers: str                          # the flights, as seats.aero lists them: "UA990, LH1114"
    duration_min: int                            # the whole trip, in minutes; 0 when not given
    departs_at: str                              # the first departure, as seats.aero gives it
    arrives_at: str                              # the last arrival, as seats.aero gives it
    seats: Optional[int]                         # seats left; None when not given
    source: str                                  # the mileage program: aeroplan, united, ...
    segments: Tuple[Segment, ...] = ()           # the flights, in order
    booking_links: Tuple[BookingLink, ...] = ()  # where to book; () for trips a search returned inline

    @property
    def taxes(self) -> str:
        """The taxes with their ISO code, as award_value reads them: "56.20 CAD"."""
        return _taxes_label(self.taxes_cents, self.taxes_currency, self.taxes_symbol)

    @property
    def duration(self) -> str:
        """The whole trip, "11h 20m"; "" when not given."""
        minutes = self.duration_min
        return f"{minutes // 60}h {minutes % 60}m" if minutes else ""

    @property
    def connections(self) -> Tuple[str, ...]:
        """The airports between the first departure and the last arrival, in order: where each
        flight lands, and where the next one leaves when that's another airport."""
        airports: List[str] = []
        for landing, leaving in zip(self.segments, self.segments[1:]):
            for code in (landing.destination, leaving.origin):
                if code not in airports:
                    airports.append(code)
        return tuple(airports)


@dataclass(frozen=True)
class Availability:
    """One cached search result: a route, a day and a program, with the cabins that have seats."""
    id: str                                     # the availability id, which `trips` takes
    origin: str                                 # IATA code
    destination: str                            # IATA code
    date: str                                   # the departure day, YYYY-MM-DD
    source: str                                 # the mileage program: aeroplan, united, ...
    distance: Optional[int] = None              # the route's length in miles; None when not given
    cabins: Tuple[CabinAvailability, ...] = ()  # the cabins with seats, economy to first
    trips: Tuple[Trip, ...] = ()                # its itineraries, when the search asked for them

    def cabin(self, name: str) -> Optional[CabinAvailability]:
        """The named cabin, when it has seats."""
        return next((cabin for cabin in self.cabins if cabin.cabin == name), None)


@dataclass(frozen=True)
class SearchPage:
    """One page of a cached search."""
    availability: Tuple[Availability, ...] = ()  # the results, in seats.aero's order
    has_more: bool = False                       # whether more results follow this page
    cursor: Optional[int] = None                 # where the next page starts, as seats.aero gives it


def cached_search(
    origin_airport: str,
    destination_airport: str,
    start_date: str = "",
    end_date: str = "",
    cabins: str = "",
    sources: str = "",
    carriers: str = "",
    only_direct_flights: bool = False,
    order_by: str = "",
    take: int = 10,
    skip: int = 0,
    cursor: Optional[int] = None,
    include_trips: bool = False,
    include_filtered: bool = False,
) -> SearchPage:
    """One page of cached availability between airports (comma-separated codes) over a date
    range. Raises SeatsAeroError."""
    params = {
        "origin_airport": origin_airport.strip().upper(),
        "destination_airport": destination_airport.strip().upper(),
        "take": min(max(take, 10), MAX_TAKE),
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

    raw_data = _get("/search", params)
    return SearchPage(
        availability=tuple(_availability(item) for item in raw_data.get("data") or []),
        has_more=bool(raw_data.get("hasMore", False)),
        cursor=raw_data.get("cursor"),
    )


def trips(availability_id: str, include_filtered: bool = False) -> List[Trip]:
    """An availability's itineraries, each with the availability's booking links. Raises
    SeatsAeroError, with status 404 when the availability is gone."""
    params = {}
    if include_filtered:
        params["include_filtered"] = "true"
    raw_data = _get(f"/trips/{availability_id.strip()}", params,
                    not_found=f"Availability object '{availability_id}' not found or has expired.")
    links = tuple(_booking_link(link) for link in raw_data.get("booking_links") or [] if isinstance(link, dict))
    return [_trip(trip, links) for trip in raw_data.get("data") or []]


def _headers() -> Dict[str, str]:
    api_key = Config().seats_aero_api_key
    if not api_key:
        raise SeatsAeroError(NO_KEY)
    return {
        "Partner-Authorization": api_key,
        "Accept": "application/json",
    }


def _get(path: str, params: Dict[str, Any], timeout: int = 20, not_found: str = "") -> Any:
    """GETs a partner API path and returns its JSON. Raises SeatsAeroError: with `not_found`
    as its message on a 404 when that's given."""
    headers = _headers()
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=timeout)
        if not_found and resp.status_code == 404:
            raise SeatsAeroError(not_found, status=404)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise SeatsAeroError(_failure_text(e), status=_status(e)) from e


def _status(error: BaseException) -> Optional[int]:
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _failure_text(error: requests.RequestException) -> str:
    """A failed request in words, with seats.aero's response when one came back."""
    text = f"Network or HTTP error communicating with Seats.aero: {error}"
    if getattr(error, "response", None) is not None:
        try:
            text += f" Response: {json.dumps(error.response.json())}"
        except Exception:
            text += f" Status: {error.response.status_code} Body: {error.response.text[:300]}"
    return text


def _int(value: Any) -> Optional[int]:
    """A whole number from seats.aero's number or digit string; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _cabin(item: Dict[str, Any], name: str, prefix: str) -> CabinAvailability:
    return CabinAvailability(
        cabin=name,
        miles=_int(item.get(f"{prefix}MileageCost", 0)),
        seats=_int(item.get(f"{prefix}RemainingSeats")) or 0,
        direct=bool(item.get(f"{prefix}Direct")),
        airlines=_str(item.get(f"{prefix}Airlines")),
        taxes_cents=_int(item.get(f"{prefix}TotalTaxes")) or 0,
        taxes_currency=_str(item.get("TaxesCurrency")),
        taxes_symbol=_str(item.get("TaxesCurrencySymbol")),
    )


def _availability(item: Dict[str, Any]) -> Availability:
    route = item.get("Route") or {}
    return Availability(
        id=_str(item.get("ID")),
        origin=_str(route.get("OriginAirport")),
        destination=_str(route.get("DestinationAirport")),
        date=_str(item.get("Date")),
        source=_str(item.get("Source") or route.get("Source")),
        distance=_int(route.get("Distance")),
        cabins=tuple(_cabin(item, name, prefix) for name, prefix in CABINS if item.get(f"{prefix}Available")),
        trips=tuple(_trip(trip) for trip in item.get("AvailabilityTrips") or []),
    )


def _segment(raw: Dict[str, Any]) -> Segment:
    return Segment(
        flight_number=_str(raw.get("FlightNumber")),
        origin=_str(raw.get("OriginAirport")),
        destination=_str(raw.get("DestinationAirport")),
        departs_at=_str(raw.get("DepartsAt")),
        arrives_at=_str(raw.get("ArrivesAt")),
    )


def _booking_link(raw: Dict[str, Any]) -> BookingLink:
    return BookingLink(label=_str(raw.get("label")), link=_str(raw.get("link")), primary=bool(raw.get("primary")))


def _trip(raw: Dict[str, Any], booking_links: Tuple[BookingLink, ...] = ()) -> Trip:
    return Trip(
        id=_str(raw.get("ID")),
        cabin=_str(raw.get("Cabin")),
        miles=_int(raw.get("MileageCost")),
        taxes_cents=_int(raw.get("TotalTaxes")) or 0,
        taxes_currency=_str(raw.get("TaxesCurrency")),
        taxes_symbol=_str(raw.get("TaxesCurrencySymbol")),
        stops=_int(raw.get("Stops")),
        carriers=_str(raw.get("Carriers")),
        flight_numbers=_str(raw.get("FlightNumbers")),
        duration_min=_int(raw.get("TotalDuration")) or 0,
        departs_at=_str(raw.get("DepartsAt")),
        arrives_at=_str(raw.get("ArrivesAt")),
        seats=_int(raw.get("RemainingSeats")),
        source=_str(raw.get("Source")),
        segments=tuple(_segment(segment) for segment in raw.get("AvailabilitySegments") or []),
        booking_links=booking_links,
    )


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
    take: int = 10,
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
) -> str:
    """
    Search and inspect award flight availability across 20+ mileage programs using the Seats.aero partner API.

    Supported Actions:
    - 'search' (or 'cached_search'): Searches cached award availability between airport pairs and date ranges.
        Args: origin_airport, destination_airport, start_date, end_date (YYYY-MM-DD).
        Optional args: cabins ('economy,premium,business,first'), sources ('aeroplan,united,american,flyingblue,...'),
        carriers ('NH,JL,BA,...'), only_direct_flights (bool), order_by ('lowest_mileage'), take, skip, cursor,
        include_trips (bool), include_filtered (bool).
    - 'trips' (or 'get_trips'): Retrieves flight-level itinerary details (flight numbers, duration, stops, exact taxes with their ISO currency code, aircraft, booking links) from an Availability object ID.
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
    """
    # Permission verification. Identity comes from the execution context, so this can no
    # longer be skipped by omitting an argument.
    ctx = try_context()
    if ctx is not None:
        from core.loaders.tools_loader import ToolsLoader
        tools_loader = ToolsLoader()
        if not tools_loader.check_permission(ctx, "seats_aero", action):
            return format_tool_response(
                "seats_aero",
                payload="",
                errors=f"Error: Agent '{ctx.agent_id}' does not have permission to execute action '{action}' on seats_aero."
            )

    api_key = Config().seats_aero_api_key
    if not api_key:
        return format_tool_response(
            "seats_aero",
            payload="",
            errors="Error: SEATS_AERO_API_KEY environment variable not set. Please set your Seats.aero partner API key."
        )

    headers = _headers()

    norm_action = action.lower().strip()

    try:
        if norm_action in ("search", "cached_search"):
            return _handle_cached_search(
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

    except SeatsAeroError as e:
        return format_tool_response("seats_aero", payload="", errors=str(e))
    except requests.RequestException as e:
        return format_tool_response("seats_aero", payload="", errors=_failure_text(e))
    except Exception as e:
        return format_tool_response("seats_aero", payload="", errors=f"Error in seats_aero: {e}")


def _handle_cached_search(
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

    page = cached_search(
        origin_airport=origin_airport,
        destination_airport=destination_airport,
        start_date=start_date,
        end_date=end_date,
        cabins=cabins,
        sources=sources,
        carriers=carriers,
        only_direct_flights=only_direct_flights,
        order_by=order_by,
        take=take,
        skip=skip,
        cursor=cursor,
        include_trips=include_trips,
        include_filtered=include_filtered,
    )

    summarized_items = []
    for availability in page.availability:
        summary = {
            "availability_id": availability.id,
            "route": f"{availability.origin} -> {availability.destination}",
            "distance_miles": availability.distance,
            "source_program": availability.source,
            "date": availability.date,
            "cabins": {
                cabin.cabin: {
                    "mileage_cost": cabin.miles,
                    "remaining_seats": cabin.seats,
                    "airlines": cabin.airlines,
                    "direct": cabin.direct,
                }
                for cabin in availability.cabins
            },
        }

        # Inline trip details if requested
        if include_trips and availability.trips:
            summary["trips"] = _format_trips(availability.trips)

        summarized_items.append(summary)

    payload_data = {
        "count": len(summarized_items),
        "has_more": page.has_more,
        "cursor": page.cursor,
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


def _handle_get_trips(availability_id: str, include_filtered: bool) -> str:
    if not availability_id:
        return format_tool_response("seats_aero", payload="", errors="Error: 'availability_id' is required for trips action.")

    try:
        found = trips(availability_id, include_filtered=include_filtered)
    except SeatsAeroError as e:
        if e.status != 404:
            raise
        return format_tool_response("seats_aero", payload="", errors=f"Error: {e}")

    formatted_trips = _format_trips(found)
    booking_links = found[0].booking_links if found else ()

    payload_data = {
        "availability_id": availability_id,
        "trip_count": len(formatted_trips),
        "trips": formatted_trips,
        "booking_links": [
            {"label": link.label, "link": link.link, "primary": link.primary} for link in booking_links
        ],
    }

    return format_tool_response("seats_aero", payload=json.dumps(payload_data, indent=2), errors="None")


def _format_trips_list(trips_raw: list, max_trips: int = 15) -> list:
    return _format_trips([_trip(trip) for trip in trips_raw[:max_trips]], max_trips)


def _format_trips(found: Sequence[Trip], max_trips: int = 15) -> list:
    formatted = []
    for trip in found[:max_trips]:
        segments = []
        for segment in trip.segments:
            segments.append({
                "flight_number": segment.flight_number,
                "carrier": segment.carrier,
                "from": segment.origin,
                "to": segment.destination,
                "departure_local": segment.departs_at,
                "arrival_local": segment.arrives_at,
            })

        formatted.append({
            "trip_id": trip.id,
            "cabin": trip.cabin,
            "mileage_cost": trip.miles,
            "total_taxes": trip.taxes,
            "stops": trip.stops,
            "carriers": trip.carriers,
            "flight_numbers": trip.flight_numbers,
            "total_duration": trip.duration,
            "departs_at": trip.departs_at,
            "arrives_at": trip.arrives_at,
            "remaining_seats": trip.seats,
            "source_program": trip.source,
            "segments": segments,
        })
    return formatted


def _taxes_label(taxes_cents: Any, currency: Optional[str], symbol: Optional[str]) -> str:
    """Taxes with their ISO code, as award_value reads them: "56.20 CAD".

    Seats.aero gives taxes in cents with the currency's ISO code. Its symbol alone
    ("$") can be USD, CAD or AUD, so without a code the currency is left open.
    """
    if not taxes_cents:
        return "N/A"
    amount = f"{taxes_cents / 100:.2f}"
    code = (currency or "").strip().upper()
    if len(code) == 3 and code.isascii() and code.isalpha():
        return f"{amount} {code}"
    shown = f", shown as '{symbol}'" if symbol else ""
    return f"{amount} (currency not given{shown})"


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
    total_count = len(routes) if isinstance(routes, list) else 0
    trimmed_routes = routes[:25] if isinstance(routes, list) and len(routes) > 25 else routes
    payload_data = {
        "source": source,
        "route_count": total_count,
        "returned_count": len(trimmed_routes) if isinstance(trimmed_routes, list) else 0,
        "routes": trimmed_routes,
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
