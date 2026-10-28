"""agents/reward-travel/preconditions/award_watch.py: Miles' award watch, a follow-up's scan check."""
import json
import os
import random
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import requests

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from tools.seats_aero import Availability, CabinAvailability, SearchPage, SeatsAeroError, Segment, Trip
from agents.reward_travel.preconditions.award_watch import MAX_TRIP_LOOKUPS, AwardWatch, Watch

ZONE = ZoneInfo("America/Los_Angeles")
FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "fixtures", "seats_aero")
WATCH = {"origins": ["SFO", "OAK"], "destinations": ["MAD", "BCN"], "depart": ["2026-12-01", "2026-12-07"]}


def cabin(name="business", miles=70000, seats=4, direct=False, airlines="IB", taxes=5620, currency="USD"):
    return CabinAvailability(name, miles, seats, direct, airlines, taxes, currency, "$")


def found(availability_id, *cabins, origin="SFO", destination="MAD", day="2026-12-05", source="iberia"):
    return Availability(id=availability_id, origin=origin, destination=destination, date=day, source=source,
                        cabins=cabins)


def trip(*airports, cabin="business", miles=70000, seats=4, stops=None, taxes=5620):
    """A trip flying `airports` in order, one flight a leg."""
    legs = tuple(Segment(f"UA{100 + n}", a, b, "", "") for n, (a, b) in enumerate(zip(airports, airports[1:])))
    return Trip(id="-".join(airports), cabin=cabin, miles=miles, taxes_cents=taxes, taxes_currency="USD",
                taxes_symbol="$", stops=len(legs) - 1 if stops is None else stops, carriers="UA",
                flight_numbers=", ".join(leg.flight_number for leg in legs), duration_min=600, departs_at="",
                arrives_at="", seats=seats, source="united", segments=legs)


class SeatsAero:
    """seats.aero's data functions, stood in for: what they return, and what they were asked."""

    def __init__(self, results=(), trips=None, has_more=False):
        self.page = SearchPage(tuple(results), has_more=has_more)
        self.by_id = trips or {}
        self.searches, self.lookups = [], []

    def cached_search(self, **kwargs):
        self.searches.append(kwargs)
        return self.page

    def trips(self, availability_id):
        self.lookups.append(availability_id)
        value = self.by_id.get(availability_id, [])
        if isinstance(value, Exception):
            raise value
        return value


def _response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status} Error", response=response)
    return response


def _recorded(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class AwardWatchCase(unittest.TestCase):

    def setUp(self):
        for patcher in (patch("core.tooling.get_local_timezone", return_value=ZONE),
                        patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def evaluate(self, args=None, seats_aero=None, day=28, month=10):
        watch = AwardWatch({"type": "award_watch", "args": {**WATCH, **(args or {})}})
        now = datetime(2026, month, day, 9, 0, tzinfo=ZONE).timestamp()
        ctx = ScheduleContext(schedule_id="followup:12", agent_id="reward-travel", now=now)
        if seats_aero is None:
            return watch.evaluate(ctx)
        with patch("tools.seats_aero.cached_search", seats_aero.cached_search), \
                patch("tools.seats_aero.trips", seats_aero.trips):
            return watch.evaluate(ctx)


class TestTheArgs(AwardWatchCase):

    def test_is_declared_by_its_type_for_reward_travel(self):
        built = build_precondition({"type": "award_watch", "args": WATCH}, agent_id="reward-travel")
        self.assertIsInstance(built, AwardWatch)
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "award_watch", "args": WATCH}, agent_id="market-scout")

    def test_defaults_and_one_form(self):
        built = AwardWatch({"type": "award_watch", "args": {
            "origins": ["sfo", "OAK", "SFO"], "destinations": [" mad "], "depart": ["2026-12-05", "2026-12-05"]}})
        self.assertEqual(built.watch, Watch(origins=("SFO", "OAK"), destinations=("MAD",), depart_from="2026-12-05",
                                            depart_to="2026-12-05"))
        self.assertEqual((built.watch.cabins, built.watch.seats, built.watch.connections), (("business",), 1, "any"))
        full = AwardWatch({"type": "award_watch", "args": {
            **WATCH, "cabins": ["First", "business"], "seats": 4, "programs": ["Aeroplan", "united"],
            "max_miles": 90000, "connections": "us_only", "max_stops": 1}}).watch
        self.assertEqual((full.cabins, full.programs), (("business", "first"), ("aeroplan", "united")))
        self.assertEqual(full.describe(), "SFO/OAK → MAD/BCN, 2026-12-01 to 2026-12-07, business/first, 4 seats, "
                                          "on aeroplan/united, at most 90,000 miles, connections only at US "
                                          "airports, at most 1 stop")

    def test_each_bad_arg_says_what_to_fix(self):
        cases = [
            ({"origins": None}, "needs origins: a list of 1 to 6 IATA airport codes, like [\"SFO\", \"OAK\"]"),
            ({"origins": "SFO"}, "needs origins"),
            ({"origins": []}, "needs origins"),
            ({"origins": ["SFO", "OAK", "SJC", "LAX", "BUR", "SMF", "SEA"]}, "origins has 7 airports: at most 6"),
            ({"origins": ["SF"]}, "origins: 'SF' isn't an IATA airport code, three letters like SFO"),
            ({"destinations": None}, "needs destinations"),
            ({"destinations": ["MAD", 5]}, "destinations: 5 isn't an IATA airport code"),
            ({"depart": None}, 'needs depart: [from, to] as YYYY-MM-DD days, like ["2026-12-01", "2026-12-07"]'),
            ({"depart": "2026-12-05"}, "needs depart"),
            ({"depart": ["12/01/2026", "12/07/2026"]}, "needs depart"),
            ({"depart": ["2027-02-30", "2027-03-01"]}, "depart: 2027-02-30 isn't a date"),
            ({"depart": ["2026-12-07", "2026-12-01"]}, "depart: from 2026-12-07 is after to 2026-12-01"),
            ({"cabins": ["coach"]}, "cabins: 'coach' isn't a cabin: pick from economy, premium, business, first"),
            ({"cabins": []}, "cabins: a list of economy, premium, business, first"),
            ({"seats": 0}, "seats is a whole number from 1 to 9"),
            ({"seats": 10}, "seats is a whole number from 1 to 9"),
            ({"seats": True}, "seats is a whole number from 1 to 9"),
            ({"seats": "2"}, "seats is a whole number from 1 to 9"),
            ({"programs": "aeroplan"}, "programs: a list of seats.aero sources, like [\"aeroplan\", \"united\"]"),
            ({"programs": ["Air Canada"]}, "programs: a list of seats.aero sources"),
            ({"max_miles": 0}, "max_miles is a whole number of miles per person, like 80000"),
            ({"max_miles": "80k"}, "max_miles is a whole number"),
            ({"connections": "us"}, "connections is any, nonstop or us_only"),
            ({"max_stops": -1}, "max_stops is a whole number, 0 or more"),
            ({"origin": "SFO"}, "doesn't take origin: its args are origins, destinations, depart"),
        ]
        for change, error in cases:
            with self.subTest(change=change):
                with self.assertRaises(PreconditionError) as caught:
                    AwardWatch({"type": "award_watch", "args": {**WATCH, **change}})
                self.assertTrue(str(caught.exception).startswith("'award_watch' "), str(caught.exception))
                self.assertIn(error, str(caught.exception))
        with self.assertRaises(PreconditionError):
            AwardWatch({"type": "award_watch", "args": ["SFO"]})


class TestTheSearchAndFilters(AwardWatchCase):

    def test_one_search_and_the_filters_seats_aero_cant_apply(self):
        seats_aero = SeatsAero([
            found("fits", cabin(miles=68000, seats=4, direct=True), day="2026-12-05"),
            found("two_left", cabin(miles=60000, seats=2), destination="BCN", source="united"),
            found("count_unknown", cabin(miles=55000, seats=0), origin="OAK", source="aeroplan"),
            found("economy_only", cabin("economy", miles=30000, seats=9)),
            found("too_dear", cabin(miles=120000, seats=9), day="2026-12-06"),
        ])
        decision = self.evaluate({"seats": 4, "max_miles": 100000, "programs": ["iberia", "united"]}, seats_aero)
        self.assertEqual(seats_aero.searches, [{
            "origin_airport": "SFO,OAK", "destination_airport": "MAD,BCN", "start_date": "2026-12-01",
            "end_date": "2026-12-07", "cabins": "business", "sources": "iberia,united", "only_direct_flights": False,
            "order_by": "lowest_mileage", "take": 1000}])
        self.assertEqual(seats_aero.lookups, [])  # connections "any" and no stop limit: no trips read
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "1 award match")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual(decision.detail.text.splitlines(), [
            "Award seats that fit your watch (SFO/OAK → MAD/BCN, 2026-12-01 to 2026-12-07, business, 4 seats, "
            "on iberia/united, at most 100,000 miles), cheapest first:",
            "- 2026-12-05 · SFO→MAD · iberia · business · 68,000 miles + 56.20 USD · 4 seats left · nonstop on IB "
            "· id fits"])

    def test_seats_left_not_given_pass_when_one_seat_is_needed(self):
        seats_aero = SeatsAero([found("count_unknown", cabin(miles=55000, seats=0, airlines="AC, LH"))])
        decision = self.evaluate({}, seats_aero)
        self.assertEqual(decision.detail.text.splitlines()[1],
                         "- 2026-12-05 · SFO→MAD · iberia · business · 55,000 miles + 56.20 USD · seats left not "
                         "given · connecting, on AC, LH · id count_unknown")
        self.assertFalse(self.evaluate({"seats": 2}, seats_aero))

    def test_every_cabin_asked_for(self):
        seats_aero = SeatsAero([found("a", cabin("economy", miles=30000, taxes=0), cabin("first", miles=110000),
                                      cabin("business", miles=70000))])
        decision = self.evaluate({"cabins": ["economy", "first"]}, seats_aero)
        self.assertEqual(seats_aero.searches[0]["cabins"], "economy,first")
        lines = decision.detail.text.splitlines()[1:]
        self.assertEqual(len(lines), 2)
        self.assertIn("· economy · 30,000 miles, taxes not given ·", lines[0])
        self.assertIn("· first · 110,000 miles + 56.20 USD ·", lines[1])

    def test_no_match(self):
        decision = self.evaluate({}, SeatsAero([found("x", cabin("economy"))]))
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "no award seats match")
        self.assertIsNone(decision.detail)

    def test_the_search_starts_today_once_the_first_day_has_passed(self):
        seats_aero = SeatsAero()
        self.evaluate({}, seats_aero, day=3, month=12)
        self.assertEqual((seats_aero.searches[0]["start_date"], seats_aero.searches[0]["end_date"]),
                         ("2026-12-03", "2026-12-07"))

    def test_nonstop(self):
        seats_aero = SeatsAero([found("direct", cabin(direct=True, miles=80000)),
                                found("connecting", cabin(direct=False, miles=60000))])
        decision = self.evaluate({"connections": "nonstop"}, seats_aero)
        self.assertTrue(seats_aero.searches[0]["only_direct_flights"])
        self.assertEqual(seats_aero.lookups, [])
        lines = decision.detail.text.splitlines()[1:]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("· nonstop on IB · id direct"))

    def test_more_results_than_one_search_reads_is_an_issue(self):
        decision = self.evaluate({}, SeatsAero([found("a", cabin())], has_more=True))
        self.assertTrue(decision)
        self.assertEqual(decision.detail.issues, ("seats.aero: more than 1000 results, and only the first 1000 "
                                                  "were read: narrow the watch to fewer airports, days or programs",))


class TestConnectionsAndStops(AwardWatchCase):

    def test_us_only_connections(self):
        seats_aero = SeatsAero(
            [found("via_ord", cabin(miles=80000), destination="BCN", source="united"),
             found("via_lhr", cabin(miles=60000), source="american"),
             found("nonstop", cabin(miles=90000, direct=True)),
             found("mixed", cabin(miles=85000), day="2026-12-06")],
            trips={
                "via_ord": [trip("SFO", "ORD", "BCN", miles=80000, seats=2)],
                "via_lhr": [trip("SFO", "LHR", "MAD", miles=60000)],
                "nonstop": [trip("SFO", "MAD", miles=90000)],
                "mixed": [trip("SFO", "LHR", "MAD", miles=85000), trip("SFO", "JFK", "MAD", miles=95000),
                          trip("SFO", "EWR", "MAD", cabin="economy", miles=40000)],
            })
        decision = self.evaluate({"connections": "us_only"}, seats_aero)
        self.assertEqual(seats_aero.lookups, ["via_lhr", "via_ord", "mixed", "nonstop"])  # the cheapest first
        lines = decision.detail.text.splitlines()[1:]
        self.assertEqual(lines, [
            "- 2026-12-05 · SFO→BCN · united · business · 80,000 miles + 56.20 USD · 2 seats left · UA100, UA101 "
            "via ORD · id via_ord",
            "- 2026-12-05 · SFO→MAD · iberia · business · 90,000 miles + 56.20 USD · 4 seats left · UA100 nonstop "
            "· id nonstop",
            "- 2026-12-06 · SFO→MAD · iberia · business · 95,000 miles + 56.20 USD · 4 seats left · UA100, UA101 "
            "via JFK · id mixed",  # its cheapest trip in the cabin that connects in the US
        ])

    def test_a_trip_must_fit_the_seats_and_miles_too(self):
        seats_aero = SeatsAero([found("a", cabin(miles=70000, seats=4))],
                               trips={"a": [trip("SFO", "ORD", "MAD", seats=1), trip("SFO", "DEN", "MAD", miles=99000),
                                            trip("SFO", "IAD", "MAD", miles=75000, seats=3)]})
        decision = self.evaluate({"connections": "us_only", "seats": 3, "max_miles": 80000}, seats_aero)
        self.assertEqual(len(decision.detail.text.splitlines()), 2)
        self.assertIn("· 75,000 miles + 56.20 USD · 3 seats left · UA100, UA101 via IAD ·", decision.detail.text)

    def test_a_connection_that_cant_be_told_doesnt_fit(self):
        unknown = Trip(id="t", cabin="business", miles=70000, taxes_cents=0, taxes_currency="", taxes_symbol="",
                       stops=1, carriers="UA", flight_numbers="UA1, UA2", duration_min=0, departs_at="", arrives_at="",
                       seats=4, source="united", segments=())
        decision = self.evaluate({"connections": "us_only"}, SeatsAero([found("a", cabin())], trips={"a": [unknown]}))
        self.assertFalse(decision)

    def test_an_airport_change_counts_both_airports(self):
        legs = (Segment("B6915", "SFO", "JFK", "", ""), Segment("IB6250", "LGA", "MAD", "", ""))
        change = Trip(id="t", cabin="business", miles=70000, taxes_cents=0, taxes_currency="", taxes_symbol="",
                      stops=1, carriers="B6, IB", flight_numbers="B6915, IB6250", duration_min=0, departs_at="",
                      arrives_at="", seats=4, source="iberia", segments=legs)
        decision = self.evaluate({"connections": "us_only"}, SeatsAero([found("a", cabin())], trips={"a": [change]}))
        self.assertIn("B6915, IB6250 via JFK, LGA", decision.detail.text)

    def test_max_stops(self):
        seats_aero = SeatsAero([found("a", cabin(miles=60000)), found("b", cabin(miles=70000)),
                                found("c", cabin(miles=80000))],
                               trips={"a": [trip("SFO", "ORD", "LHR", "MAD")], "b": [trip("SFO", "LHR", "MAD")],
                                      "c": [trip("SFO", "MAD")]})
        one = self.evaluate({"max_stops": 1}, seats_aero).detail.text  # any connection, at most one
        self.assertNotIn("id a", one)
        self.assertIn("UA100, UA101 via LHR · id b", one)
        self.assertIn("UA100 nonstop · id c", one)
        none = self.evaluate({"max_stops": 0}, seats_aero).detail.text.splitlines()[1:]
        self.assertEqual(len(none), 1)
        self.assertTrue(none[0].endswith("id c"))

    def test_trips_read_at_most_the_cap_cheapest_first(self):
        results = [found(f"a{n:02}", cabin(miles=60000 + n * 1000)) for n in range(12)]
        seats_aero = SeatsAero(results, trips={f"a{n:02}": [trip("SFO", "LHR", "MAD")] for n in range(12)})
        decision = self.evaluate({"connections": "us_only"}, seats_aero)
        self.assertEqual(seats_aero.lookups, [f"a{n:02}" for n in range(MAX_TRIP_LOOKUPS)])
        self.assertFalse(decision)
        self.assertIsInstance(decision.detail, Problems)  # "no match" may only mean "couldn't check": posted
        self.assertEqual(decision.detail.issues, (
            "seats.aero: more candidates than 8 trip lookups a check cover, so the rest weren't checked for "
            "connections: narrow the watch with max_miles, or fewer airports or programs",))

    def test_an_availability_gone_since_the_search_is_skipped(self):
        gone = SeatsAeroError("Availability object 'a' not found or has expired.", status=404)
        seats_aero = SeatsAero([found("a", cabin(miles=60000)), found("b", cabin(miles=70000))],
                               trips={"a": gone, "b": [trip("SFO", "ORD", "MAD")]})
        decision = self.evaluate({"connections": "us_only"}, seats_aero)
        self.assertEqual(seats_aero.lookups, ["a", "b"])
        self.assertEqual(decision.reason, "1 award match")


class TestTheFindings(AwardWatchCase):

    def results(self):
        return [found(f"id{n:02}", cabin(miles=50000 + (n % 4) * 5000, seats=2 + n % 3), day=f"2026-12-0{1 + n % 7}",
                      source=("iberia", "united", "aeroplan")[n % 3]) for n in range(14)]

    def test_cheapest_first_at_most_ten(self):
        decision = self.evaluate({}, SeatsAero(self.results()))
        lines = decision.detail.text.splitlines()
        self.assertEqual(decision.reason, "14 award matches")
        self.assertEqual(len(lines), 1 + 10 + 1)
        self.assertEqual(lines[-1], "More fit: these are the 10 cheapest.")
        miles = [int(line.split(" · ")[4].split(" miles")[0].replace(",", "")) for line in lines[1:-1]]
        self.assertEqual(miles, sorted(miles))

    def test_the_same_seats_in_any_order_give_the_same_fingerprint(self):
        from core.proactive.follow_ups import fingerprint
        results = self.results()
        first = self.evaluate({}, SeatsAero(results))
        shuffled = results[:]
        random.Random(7).shuffle(shuffled)
        again = self.evaluate({}, SeatsAero(shuffled))
        self.assertEqual(first.detail.text, again.detail.text)
        self.assertEqual(fingerprint(first), fingerprint(again))
        fewer = results[:]
        fewer[0] = found("id00", cabin(miles=50000, seats=1), day="2026-12-01", source="iberia")  # a seat went
        self.assertNotEqual(fingerprint(self.evaluate({}, SeatsAero(fewer))), fingerprint(first))


class TestWhatItCantRead(AwardWatchCase):

    def test_no_key_is_posted(self):
        seats_aero = SeatsAero()
        with patch.dict(os.environ, {"SEATS_AERO_API_KEY": "", "SEATSAERO_API_KEY": ""}):
            decision = self.evaluate({}, seats_aero)
        self.assertFalse(decision)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.reason, "award_watch can't run: seats.aero: SEATS_AERO_API_KEY is not set")
        self.assertEqual(seats_aero.searches, [])

    def test_days_that_have_all_passed_are_posted(self):
        decision = self.evaluate({}, SeatsAero(), day=8, month=12)
        self.assertIsInstance(decision.detail, Problems)
        self.assertEqual(decision.detail.issues, ("depart: 2026-12-01 to 2026-12-07 has passed: close this watch",))

    @patch("tools.seats_aero.requests.get")
    def test_a_refused_key_or_spent_quota_is_posted(self, mock_get):
        for status, issue in ((401, "seats.aero: refused the API key (401): check SEATS_AERO_API_KEY"),
                              (429, "seats.aero: says the request quota is spent (429): checks go on when it resets")):
            with self.subTest(status=status):
                mock_get.return_value = _response({"error": "no"}, status=status)
                decision = self.evaluate()
                self.assertIsInstance(decision.detail, Problems)
                self.assertEqual(decision.detail.issues, (issue,))

    @patch("tools.seats_aero.requests.get")
    def test_a_hiccup_is_only_logged(self, mock_get):
        for failure in (_response(None, status=503), requests.Timeout("read timed out")):
            with self.subTest(failure=failure):
                mock_get.return_value, mock_get.side_effect = (failure, None) if isinstance(failure, MagicMock) \
                    else (None, failure)
                decision = self.evaluate()
                self.assertFalse(decision)
                self.assertIsNone(decision.detail)  # nothing posted: the next check retries
                self.assertTrue(decision.reason.startswith("award_watch failed (SeatsAeroError: "), decision.reason)


class TestOnRecordedResponses(AwardWatchCase):

    @patch("tools.seats_aero.requests.get")
    def test_us_only_through_the_data_functions(self, mock_get):
        nonstop = {"data": [{"ID": "t_ib", "Cabin": "business", "MileageCost": 68000, "TotalTaxes": 21310,
                             "TaxesCurrency": "EUR", "Stops": 0, "FlightNumbers": "IB6170", "RemainingSeats": 4,
                             "AvailabilitySegments": [{"FlightNumber": "IB6170", "OriginAirport": "SFO",
                                                       "DestinationAirport": "MAD"}]}]}
        via_toronto = {"data": [{"ID": "t_ac", "Cabin": "business", "MileageCost": 70000, "Stops": 1,
                                 "FlightNumbers": "AC738, AC836", "RemainingSeats": 2,
                                 "AvailabilitySegments": [
                                     {"FlightNumber": "AC738", "OriginAirport": "OAK", "DestinationAirport": "YYZ"},
                                     {"FlightNumber": "AC836", "OriginAirport": "YYZ", "DestinationAirport": "BCN"}]}]}
        responses = {"/search": _recorded("search.json"), "/trips/2pYQ6Vx1sKfA9bLmN3cR7tWzE0u": nonstop,
                     "/trips/2pYQ8Hn5qZeD1rVsM6wY9tJuG3b": via_toronto,
                     "/trips/2pYQ7Kd3mWcB0nTqL5vX8rHsF2a": _recorded("trips.json")}
        mock_get.side_effect = lambda url, **kwargs: _response(responses[url.split("/partnerapi")[1]])

        decision = self.evaluate({"programs": ["iberia", "united", "aeroplan"], "connections": "us_only"})
        self.assertEqual(mock_get.call_args_list[0][1]["params"]["sources"], "iberia,united,aeroplan")
        self.assertEqual(decision.detail.text.splitlines()[1:], [
            "- 2026-12-05 · SFO→MAD · iberia · business · 68,000 miles + 213.10 EUR · 4 seats left · IB6170 nonstop "
            "· id 2pYQ6Vx1sKfA9bLmN3cR7tWzE0u",
            "- 2026-12-06 · SFO→BCN · united · business · 88,000 miles + 5.60 USD · 2 seats left · UA1580, UA121 "
            "via EWR · id 2pYQ7Kd3mWcB0nTqL5vX8rHsF2a",  # its trip via Frankfurt doesn't fit
        ])
