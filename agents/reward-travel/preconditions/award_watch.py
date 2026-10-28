"""award_watch: Miles watches award seats for one trip, and speaks up only when some fit.

It's a follow-up's check (core/proactive/follow_ups.py), opened with
{"type": "award_watch", "args": {...}} (tools/follow_up.py). Each check runs in
code, with no model: one seats.aero cached search over every origin and
destination for the whole date range, then the filters seats.aero can't apply.
Only new matches start Miles' turn. He values them with award_value, as his
flight_search skill says: a cents-per-point value needs a cash fare, and
seats.aero doesn't give one.

Args, checked when the follow-up opens and again at each check:

    origins, destinations  1 to 6 IATA codes each: ["SFO", "OAK"]
    depart                 [from, to], YYYY-MM-DD, from <= to: the departure days
    cabins                 of economy, premium, business, first; ["business"] if left out
    seats                  1 to 9, 1 if left out: the seats left needed in the cabin
    programs               seats.aero sources, ["aeroplan", "united"]; every program if left out
    max_miles              the most miles per person; no limit if left out
    connections            "any" (the default); "nonstop"; or "us_only": every
                           connection at a US airport (lib/airports.py), so a nonstop passes
    max_stops              the most connections; no limit if left out

A check:

  1. One cached search (tools/seats_aero.cached_search): the airports
     comma-joined, the date range from today on, the cabins and programs, and
     only_direct_flights for "nonstop". It reads at most MAX_TAKE results;
     more than that is an issue: narrow the watch.
  2. In code, each cabin asked for that has seats: enough seats left, at most
     max_miles, and a nonstop in that cabin for "nonstop". seats.aero gives 0
     seats left when it doesn't know, which passes only when one seat is needed.
  3. For "us_only" or max_stops, it reads the itineraries (tools/seats_aero.trips)
     of the cheapest of those, at most MAX_TRIP_LOOKUPS availabilities a check.
     It keeps the trips in that cabin with enough seats, within max_miles, whose
     connections are all at US airports (for "us_only") and whose stops fit; a
     match shows its cheapest such trip. A trip that connects without saying
     where can't be checked, so it doesn't fit. Candidates past the lookup cap
     go unchecked, and an issue says so.

Findings: the watch in a line, then one line per match, cheapest first, at most
MAX_MATCHES: the day, route, program, cabin, miles and taxes, seats left, the
flights with their connection airports or "nonstop", and the availability id,
for `trips`. Nothing in them changes while the seats don't (no "updated at"),
so the follow-up's fingerprint changes only when availability does. No match:
"no award seats match".

What it couldn't read goes in the report (core/scheduler/preconditions/scan.py):
no SEATS_AERO_API_KEY, or departure days that have all passed, skip the check
and are posted; so is seats.aero refusing the call (a 401 key, a 429 quota, any
other 4xx). A timeout, a dropped connection or a 5xx is only logged, and the
next check retries.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core.scheduler.preconditions.base import PreconditionError
from core.scheduler.preconditions.scan import Scan, is_network_error
from core.scheduler.spec import ScheduleContext, WorkDecision
from core.tooling import Report

if TYPE_CHECKING:  # imported when a check runs (scan)
    from tools.seats_aero import Availability, CabinAvailability, Trip

TYPE = "award_watch"
CABINS = ("economy", "premium", "business", "first")
CONNECTIONS = ("any", "nonstop", "us_only")
ARGS = ("origins", "destinations", "depart", "cabins", "seats", "programs", "max_miles", "connections", "max_stops")
# The most airports on each side of a watch, and the most seats it can need.
MAX_AIRPORTS, MAX_SEATS = 6, 9
# Availabilities whose itineraries one check reads, the cheapest first.
MAX_TRIP_LOOKUPS = 8
# Matches the findings list, the cheapest first.
MAX_MATCHES = 10
# How an issue names the service.
SEATS_AERO = "seats.aero"

_IATA = re.compile(r"^[A-Za-z]{3}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROGRAM = re.compile(r"^[a-z][a-z0-9_]*$")
_NO_MILES = float("inf")  # sorts an award without a price last


@dataclass(frozen=True)
class Watch:
    """An award watch's args, checked and in one form (`read_watch`)."""
    origins: Tuple[str, ...]                 # IATA codes, upper case, each once
    destinations: Tuple[str, ...]            # IATA codes, upper case, each once
    depart_from: str                         # the first departure day, YYYY-MM-DD
    depart_to: str                           # the last departure day, YYYY-MM-DD
    cabins: Tuple[str, ...] = ("business",)  # of CABINS, in their order
    seats: int = 1                           # the seats left needed in a cabin
    programs: Tuple[str, ...] = ()           # seats.aero sources; () for every program
    max_miles: Optional[int] = None          # the most miles per person; None for no limit
    connections: str = "any"                 # any, nonstop or us_only
    max_stops: Optional[int] = None          # the most connections; None for no limit

    @property
    def needs_trips(self) -> bool:
        """Whether a match needs its itineraries read: for us_only, or a stop limit."""
        return self.connections == "us_only" or self.max_stops is not None

    def describe(self) -> str:
        """The watch in a few words: "SFO/OAK → MAD, 2026-12-01 to 2026-12-07, business, 4 seats"."""
        days = self.depart_from if self.depart_from == self.depart_to else f"{self.depart_from} to {self.depart_to}"
        parts = [f"{'/'.join(self.origins)} → {'/'.join(self.destinations)}", days, "/".join(self.cabins),
                 _count(self.seats, "seat")]
        if self.programs:
            parts.append("on " + "/".join(self.programs))
        if self.max_miles is not None:
            parts.append(f"at most {self.max_miles:,} miles")
        if self.connections == "nonstop":
            parts.append("nonstop")
        elif self.connections == "us_only":
            parts.append("connections only at US airports")
        if self.max_stops is not None:
            parts.append(f"at most {_count(self.max_stops, 'stop')}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Match:
    """One cabin of one availability that fits the watch, with its cheapest fitting trip
    when the watch reads trips."""
    availability: "Availability"     # the route, day and program
    cabin: "CabinAvailability"       # the cabin that fits
    trip: Optional["Trip"] = None    # its cheapest fitting itinerary; None when the watch reads no trips

    @property
    def miles(self) -> Optional[int]:
        return self.trip.miles if self.trip is not None else self.cabin.miles

    def order(self) -> tuple:
        """Cheapest first, then by day, route, program, cabin and id: the same seats always sort the same."""
        return _order(self.miles, self.availability, self.cabin.cabin)

    def line(self) -> str:
        """The match as a findings line (module docstring)."""
        availability, cabin, trip = self.availability, self.cabin, self.trip
        miles = f"{self.miles:,} miles" if self.miles is not None else "miles not given"
        taxes = trip.taxes if trip is not None else cabin.taxes
        money = f"{miles} + {taxes}" if taxes != "N/A" else f"{miles}, taxes not given"
        seats = trip.seats if trip is not None else cabin.seats
        left = f"{_count(seats, 'seat')} left" if seats else "seats left not given"
        flights = _flights(trip) if trip is not None else _airlines(cabin)
        return "- " + " · ".join([availability.date, f"{availability.origin}→{availability.destination}",
                                  availability.source, cabin.cabin, money, left, flights, f"id {availability.id}"])


class AwardWatch(Scan):
    """Award seats fit one trip's watch (module docstring)."""
    type_name = TYPE

    def validate(self):
        super().validate()
        self.watch = read_watch(self.args)

    def scan(self, ctx: ScheduleContext, report: Report, **args) -> WorkDecision:
        """`args` were read into self.watch when the scan was built."""
        from core.config.settings import Config
        from core.tooling import get_local_timezone
        from tools.seats_aero import MAX_TAKE, SeatsAeroError, cached_search, trips
        from agents.reward_travel.lib.airports import is_us_airport

        watch = self.watch
        today = datetime.fromtimestamp(ctx.now, get_local_timezone()).date().isoformat()
        if not Config().seats_aero_api_key:
            report.missing(SEATS_AERO, "SEATS_AERO_API_KEY is not set")
        if watch.depart_to < today:
            report.missing("depart", f"{watch.depart_from} to {watch.depart_to} has passed: close this watch")
        report.raise_if_failed()

        def read_trips(availability_id: str) -> List["Trip"]:
            try:
                return trips(availability_id)
            except SeatsAeroError as e:
                if e.status == 404:  # gone since the search
                    return []
                raise

        try:
            page = cached_search(
                origin_airport=",".join(watch.origins),
                destination_airport=",".join(watch.destinations),
                start_date=max(watch.depart_from, today),
                end_date=watch.depart_to,
                cabins=",".join(watch.cabins),
                sources=",".join(watch.programs),
                only_direct_flights=watch.connections == "nonstop",
                order_by="lowest_mileage",
                take=MAX_TAKE,
            )
            if page.has_more:
                report.problem(SEATS_AERO, f"more than {MAX_TAKE} results, and only the first {MAX_TAKE} were read: "
                                           "narrow the watch to fewer airports, days or programs")
            candidates = sorted(
                ((availability, cabin) for availability in page.availability for cabin in availability.cabins
                 if cabin.cabin in watch.cabins and cabin_fits(watch, cabin)),
                key=lambda pair: _order(pair[1].miles, pair[0], pair[1].cabin),
            )
            if watch.needs_trips:
                matches = matches_with_trips(watch, candidates, read_trips, is_us_airport, report)
            else:
                matches = [Match(availability, cabin) for availability, cabin in candidates]
        except SeatsAeroError as e:
            if is_network_error(e):
                raise  # a hiccup: only logged, and the next check retries
            report.missing(SEATS_AERO, refusal(e))
            report.raise_if_failed()

        if not matches:
            return WorkDecision(False, "no award seats match")
        matches.sort(key=Match.order)
        lines = [f"Award seats that fit your watch ({watch.describe()}), cheapest first:"]
        lines += [match.line() for match in matches[:MAX_MATCHES]]
        if len(matches) > MAX_MATCHES:
            lines.append(f"More fit: these are the {MAX_MATCHES} cheapest.")
        return WorkDecision(True, _count(len(matches), "award match", "award matches"), detail="\n".join(lines))


def read_watch(args: Mapping[str, Any]) -> Watch:
    """The watch in a scan's args, or a PreconditionError that says what to fix."""
    unknown = sorted(set(args) - set(ARGS))
    if unknown:
        raise _error(f"doesn't take {', '.join(unknown)}: its args are {', '.join(ARGS)}")
    origins = _airports(args.get("origins"), "origins", '["SFO", "OAK"]')
    destinations = _airports(args.get("destinations"), "destinations", '["MAD", "BCN"]')
    depart_from, depart_to = _depart(args.get("depart"))
    return Watch(
        origins=origins,
        destinations=destinations,
        depart_from=depart_from,
        depart_to=depart_to,
        cabins=_cabins(args.get("cabins")),
        seats=_seats(args.get("seats")),
        programs=_programs(args.get("programs")),
        max_miles=_max_miles(args.get("max_miles")),
        connections=_connections(args.get("connections")),
        max_stops=_max_stops(args.get("max_stops")),
    )


def enough_seats(seats_left: Optional[int], needed: int) -> bool:
    """Whether seats.aero's seats left cover the seats needed. It gives 0 (or nothing) when it
    doesn't know; seats it shows at all are at least one, so that passes when one is needed."""
    return needed == 1 or (seats_left or 0) >= needed


def within(miles: Optional[int], max_miles: Optional[int]) -> bool:
    """Whether an award's miles are within the limit; an award without a price never is."""
    return max_miles is None or (miles is not None and miles <= max_miles)


def cabin_fits(watch: Watch, cabin: "CabinAvailability") -> bool:
    """Whether a cabin with seats fits before its trips are read: seats, miles, and a nonstop for "nonstop"."""
    return (enough_seats(cabin.seats, watch.seats) and within(cabin.miles, watch.max_miles)
            and (watch.connections != "nonstop" or cabin.direct))


def connects(watch: Watch, trip: "Trip", is_us_airport: Callable[[str], bool]) -> bool:
    """Whether a trip's stops and connection airports fit the watch. Stops seats.aero doesn't
    give are counted from the flights; a trip whose stops or connections can't be told doesn't fit."""
    airports = trip.connections
    stops = trip.stops if trip.stops is not None else (len(trip.segments) - 1 if trip.segments else None)
    if stops is None:
        return False
    if watch.max_stops is not None and stops > watch.max_stops:
        return False
    if watch.connections == "nonstop":
        return not stops and not airports
    if watch.connections == "us_only":
        if stops and not airports:
            return False  # it connects, but seats.aero didn't say where
        return all(is_us_airport(code) for code in airports)
    return True


def best_trip(watch: Watch, found: Sequence["Trip"], cabin: str,
              is_us_airport: Callable[[str], bool]) -> Optional["Trip"]:
    """The cheapest of an availability's trips in `cabin` that fits the watch; None if none does."""
    fitting = [trip for trip in found
               if trip.cabin.strip().lower() == cabin and enough_seats(trip.seats, watch.seats)
               and within(trip.miles, watch.max_miles) and connects(watch, trip, is_us_airport)]
    return min(fitting, default=None, key=lambda trip: (
        trip.miles if trip.miles is not None else _NO_MILES, trip.stops or 0, trip.departs_at, trip.id))


def matches_with_trips(watch: Watch, candidates: Sequence[Tuple["Availability", "CabinAvailability"]],
                       read_trips: Callable[[str], Sequence["Trip"]],
                       is_us_airport: Callable[[str], bool], report: Report) -> List[Match]:
    """The candidates, cheapest first, whose trips fit: each availability's trips read once, at
    most MAX_TRIP_LOOKUPS availabilities. The rest go unchecked, and an issue says so."""
    found: Dict[str, Sequence["Trip"]] = {}
    matches: List[Match] = []
    unchecked = False
    for availability, cabin in candidates:
        if availability.id not in found:
            if len(found) >= MAX_TRIP_LOOKUPS:
                unchecked = True
                continue
            found[availability.id] = read_trips(availability.id)
        trip = best_trip(watch, found[availability.id], cabin.cabin, is_us_airport)
        if trip is not None:
            matches.append(Match(availability, cabin, trip))
    if unchecked:
        report.problem(SEATS_AERO, f"more candidates than {MAX_TRIP_LOOKUPS} trip lookups a check cover, so the "
                                   "rest weren't checked for connections: narrow the watch with max_miles, "
                                   "or fewer airports or programs")
    return matches


def refusal(error: Any) -> str:
    """seats.aero refusing a call, in words for the agent's channel."""
    status = error.status
    if status in (401, 403):
        return f"refused the API key ({status}): check SEATS_AERO_API_KEY"
    if status == 429:
        return "says the request quota is spent (429): checks go on when it resets"
    text = str(error)
    return f"refused the search ({status}): {text[:300]}" if status else text[:300]


def _order(miles: Optional[int], availability: "Availability", cabin: str) -> tuple:
    return (miles if miles is not None else _NO_MILES, availability.date, availability.origin,
            availability.destination, availability.source, CABINS.index(cabin), availability.id)


def _flights(trip: "Trip") -> str:
    """A trip's flights and where they connect: "UA1580, UA121 via EWR", or "IB6170 nonstop"."""
    flights = trip.flight_numbers or ", ".join(segment.flight_number for segment in trip.segments)
    airports = trip.connections
    if airports:
        where = "via " + ", ".join(airports)
    elif trip.stops:
        where = _count(trip.stops, "stop")
    else:
        where = "nonstop"
    return f"{flights} {where}".strip()


def _airlines(cabin: "CabinAvailability") -> str:
    """A cabin's airlines when its trips weren't read: "nonstop on IB", or "connecting, on UA, LH"."""
    if cabin.direct:
        return f"nonstop on {cabin.airlines}" if cabin.airlines else "nonstop"
    return f"connecting, on {cabin.airlines}" if cabin.airlines else "connecting"


def _count(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _error(what: str) -> PreconditionError:
    return PreconditionError(f"'{TYPE}' {what}")


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _airports(value: Any, key: str, example: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _error(f"needs {key}: a list of 1 to {MAX_AIRPORTS} IATA airport codes, like {example}")
    if len(value) > MAX_AIRPORTS:
        raise _error(f"{key} has {len(value)} airports: at most {MAX_AIRPORTS}")
    codes: List[str] = []
    for code in value:
        if not isinstance(code, str) or not _IATA.match(code.strip()):
            raise _error(f"{key}: {code!r} isn't an IATA airport code, three letters like SFO")
        if code.strip().upper() not in codes:
            codes.append(code.strip().upper())
    return tuple(codes)


def _depart(value: Any) -> Tuple[str, str]:
    form = 'needs depart: [from, to] as YYYY-MM-DD days, like ["2026-12-01", "2026-12-07"]'
    if not isinstance(value, list) or len(value) != 2:
        raise _error(form)
    days: List[date] = []
    for text in value:
        if not isinstance(text, str) or not _DAY.match(text.strip()):
            raise _error(form)
        try:
            days.append(date.fromisoformat(text.strip()))
        except ValueError:
            raise _error(f"depart: {text.strip()} isn't a date") from None
    if days[0] > days[1]:
        raise _error(f"depart: from {days[0]} is after to {days[1]}")
    return days[0].isoformat(), days[1].isoformat()


def _cabins(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ("business",)
    if not isinstance(value, list) or not value:
        raise _error(f'cabins: a list of {", ".join(CABINS)}, like ["business"]')
    picked = set()
    for cabin in value:
        name = cabin.strip().lower() if isinstance(cabin, str) else cabin
        if name not in CABINS:
            raise _error(f"cabins: {cabin!r} isn't a cabin: pick from {', '.join(CABINS)}")
        picked.add(name)
    return tuple(cabin for cabin in CABINS if cabin in picked)


def _seats(value: Any) -> int:
    if value is None:
        return 1
    if not _is_whole(value) or not 1 <= value <= MAX_SEATS:
        raise _error(f"seats is a whole number from 1 to {MAX_SEATS}: the seats left needed in the cabin")
    return value


def _programs(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(p, str) and _PROGRAM.match(p.strip().lower()) for p in value):
        raise _error('programs: a list of seats.aero sources, like ["aeroplan", "united"]; leave it out for every program')
    return tuple(dict.fromkeys(program.strip().lower() for program in value))


def _max_miles(value: Any) -> Optional[int]:
    if value is not None and (not _is_whole(value) or value <= 0):
        raise _error("max_miles is a whole number of miles per person, like 80000")
    return value


def _connections(value: Any) -> str:
    if value is None:
        return "any"
    name = value.strip().lower() if isinstance(value, str) else value
    if name not in CONNECTIONS:
        raise _error("connections is any, nonstop or us_only (every connection at a US airport)")
    return name


def _max_stops(value: Any) -> Optional[int]:
    if value is not None and (not _is_whole(value) or value < 0):
        raise _error("max_stops is a whole number, 0 or more")
    return value
