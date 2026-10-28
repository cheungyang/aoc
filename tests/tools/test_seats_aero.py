import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.seats_aero import seats_aero
from core.tooling import format_tool_response


class TestSeatsAeroTool(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key(self):
        result = seats_aero.invoke({"action": "search", "origin_airport": "SFO", "destination_airport": "NRT"})
        self.assertIn("SEATS_AERO_API_KEY environment variable not set", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_cached_search_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {
                    "ID": "avail_123",
                    "Route": {
                        "OriginAirport": "SFO",
                        "DestinationAirport": "HND",
                        "Distance": 5160,
                        "Source": "aeroplan"
                    },
                    "Date": "2024-11-15",
                    "YAvailable": True,
                    "YMileageCost": "35000",
                    "YRemainingSeats": 4,
                    "YAirlines": "AC, NH",
                    "YDirect": True,
                    "JAvailable": True,
                    "JMileageCost": "75000",
                    "JRemainingSeats": 2,
                    "JAirlines": "NH",
                    "JDirect": True,
                    "FAvailable": False,
                    "Source": "aeroplan"
                }
            ],
            "hasMore": False,
            "cursor": 12345
        }
        mock_get.return_value = mock_response

        result = seats_aero.invoke({
            "action": "search",
            "origin_airport": "SFO",
            "destination_airport": "HND",
            "start_date": "2024-11-01",
            "end_date": "2024-11-30",
            "cabins": "business",
            "sources": "aeroplan"
        })

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        called_params = mock_get.call_args[1]["params"]
        called_headers = mock_get.call_args[1]["headers"]

        self.assertEqual(called_url, "https://seats.aero/partnerapi/search")
        self.assertEqual(called_params["origin_airport"], "SFO")
        self.assertEqual(called_params["destination_airport"], "HND")
        self.assertEqual(called_params["start_date"], "2024-11-01")
        self.assertEqual(called_params["end_date"], "2024-11-30")
        self.assertEqual(called_params["cabins"], "business")
        self.assertEqual(called_params["sources"], "aeroplan")
        self.assertEqual(called_headers["Partner-Authorization"], "test_api_key")

        self.assertIn("avail_123", result)
        self.assertIn("SFO -> HND", result)
        self.assertIn("75000", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    def test_search_missing_airports(self):
        res1 = seats_aero.invoke({"action": "search", "origin_airport": ""})
        self.assertIn("'origin_airport' is required", res1)

        res2 = seats_aero.invoke({"action": "search", "origin_airport": "SFO", "destination_airport": ""})
        self.assertIn("'destination_airport' is required", res2)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_get_trips_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {
                    "ID": "trip_abc",
                    "Cabin": "business",
                    "MileageCost": 75000,
                    "TotalTaxes": 5620,
                    "TaxesCurrency": "USD",
                    "TaxesCurrencySymbol": "$",
                    "TotalDuration": 680,
                    "Stops": 0,
                    "Carriers": "NH",
                    "FlightNumbers": "NH107",
                    "DepartsAt": "2024-11-15T01:20:00Z",
                    "ArrivesAt": "2024-11-16T05:00:00Z",
                    "RemainingSeats": 2,
                    "Source": "aeroplan",
                    "AvailabilitySegments": [
                        {
                            "FlightNumber": "NH107",
                            "OriginAirport": "SFO",
                            "DestinationAirport": "HND",
                            "AircraftName": "77W",
                            "FareClass": "I",
                            "Distance": 5160,
                            "DepartsAt": "2024-11-15T01:20:00Z",
                            "ArrivesAt": "2024-11-16T05:00:00Z"
                        }
                    ]
                }
            ],
            "booking_links": [
                {
                    "label": "Book via Air Canada Aeroplan",
                    "link": "https://www.aircanada.com/aeroplan",
                    "primary": True
                }
            ]
        }
        mock_get.return_value = mock_response

        result = seats_aero.invoke({"action": "trips", "availability_id": "avail_123"})
        mock_get.assert_called_once_with(
            "https://seats.aero/partnerapi/trips/avail_123",
            params={},
            headers={"Partner-Authorization": "test_api_key", "Accept": "application/json"},
            timeout=20
        )
        self.assertIn("trip_abc", result)
        self.assertIn("NH107", result)
        self.assertIn('"total_taxes": "56.20 USD"', result)
        self.assertNotIn("$56.20", result)
        self.assertIn("11h 20m", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_get_destinations_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "origin_airport": "SFO",
            "destinations": [
                {
                    "airport": "LHR",
                    "economy": 30000,
                    "premium": 45000,
                    "business": 57500,
                    "first": 90000
                }
            ],
            "success": True
        }
        mock_get.return_value = mock_response

        result = seats_aero.invoke({"action": "destinations", "origin_airport": "SFO"})
        mock_get.assert_called_once_with(
            "https://seats.aero/partnerapi/destinations",
            params={"origin_airport": "SFO"},
            headers={"Partner-Authorization": "test_api_key", "Accept": "application/json"},
            timeout=20
        )
        self.assertIn("LHR", result)
        self.assertIn("57500", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    def test_get_destinations_invalid_args(self):
        # Both provided
        res = seats_aero.invoke({"action": "destinations", "origin_airport": "SFO", "destination_airport": "JFK"})
        self.assertIn("Provide exactly ONE", res)

        # Neither provided
        res2 = seats_aero.invoke({"action": "destinations"})
        self.assertIn("Provide exactly ONE", res2)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_bulk_availability(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {
                    "ID": "bulk_1",
                    "Route": {
                        "OriginAirport": "JFK",
                        "DestinationAirport": "CDG",
                        "OriginRegion": "North America",
                        "DestinationRegion": "Europe",
                        "Source": "flyingblue"
                    },
                    "Date": "2024-10-10",
                    "JAvailable": True,
                    "JMileageCost": "50000",
                    "JRemainingSeats": 3,
                    "JDirect": True,
                    "Source": "flyingblue"
                }
            ],
            "hasMore": False,
            "cursor": 999
        }
        mock_get.return_value = mock_response

        result = seats_aero.invoke({
            "action": "bulk_availability",
            "source": "flyingblue",
            "cabin": "business",
            "origin_region": "North America",
            "destination_region": "Europe"
        })
        self.assertIn("bulk_1", result)
        self.assertIn("JFK -> CDG", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.post")
    def test_refresh_action(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "items": [{"availability_id": "id1", "status": "queued"}],
            "queued": 1,
            "complete": False
        }
        mock_post.return_value = mock_response

        result = seats_aero.invoke({"action": "refresh", "availability_ids": ["id1"]})
        mock_post.assert_called_once_with(
            "https://seats.aero/partnerapi/refresh",
            json={"availability_ids": ["id1"]},
            headers={"Partner-Authorization": "test_api_key", "Accept": "application/json"},
            timeout=20
        )
        self.assertIn('"queued": 1', result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_routes_action(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {"OriginAirport": "SFO", "DestinationAirport": "NRT", "Source": "aeroplan"}
        ]
        mock_get.return_value = mock_response

        result = seats_aero.invoke({"action": "routes", "source": "aeroplan"})
        self.assertIn("SFO", result)
        self.assertIn("NRT", result)

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.post")
    def test_live_search_action(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {
                    "ID": "live_trip_1",
                    "Cabin": "business",
                    "MileageCost": 80000,
                    "TotalTaxes": 5600,
                    "TaxesCurrencySymbol": "$",
                    "TotalDuration": 600,
                    "Stops": 0,
                    "Carriers": "UA",
                    "FlightNumbers": "UA837",
                    "DepartsAt": "2025-05-20T11:00:00Z",
                    "ArrivesAt": "2025-05-21T14:30:00Z",
                    "RemainingSeats": 4,
                    "Source": "united"
                }
            ]
        }
        mock_post.return_value = mock_response

        result = seats_aero.invoke({
            "action": "live_search",
            "origin_airport": "SFO",
            "destination_airport": "NRT",
            "departure_date": "2025-05-20",
            "source": "united"
        })
        self.assertIn("live_trip_1", result)
        self.assertIn("UA837", result)


class TestTaxesLabel(unittest.TestCase):
    """Trip taxes carry their ISO currency code (money in tool output)."""

    def taxes(self, **fields):
        from tools.seats_aero import _format_trips_list
        return _format_trips_list([{"ID": "t1", **fields}])[0]["total_taxes"]

    def test_the_iso_code_from_seats_aero(self):
        self.assertEqual(self.taxes(TotalTaxes=5620, TaxesCurrency="USD", TaxesCurrencySymbol="$"), "56.20 USD")
        self.assertEqual(self.taxes(TotalTaxes=12345, TaxesCurrency="cad", TaxesCurrencySymbol="$"), "123.45 CAD")
        self.assertEqual(self.taxes(TotalTaxes=890, TaxesCurrency="AUD", TaxesCurrencySymbol="$"), "8.90 AUD")

    def test_a_symbol_alone_leaves_the_currency_open(self):
        self.assertEqual(self.taxes(TotalTaxes=5620, TaxesCurrencySymbol="$"), "56.20 (currency not given, shown as '$')")
        self.assertEqual(self.taxes(TotalTaxes=5620, TaxesCurrency=""), "56.20 (currency not given)")

    def test_no_taxes(self):
        self.assertEqual(self.taxes(TaxesCurrency="USD"), "N/A")

    @patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
    @patch("tools.seats_aero.requests.get")
    def test_search_with_trips_labels_them_too(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": [{
            "ID": "avail_1", "Route": {"OriginAirport": "YVR", "DestinationAirport": "NRT"}, "Date": "2026-11-10",
            "AvailabilityTrips": [{"ID": "trip_1", "TotalTaxes": 9810, "TaxesCurrency": "CAD", "TaxesCurrencySymbol": "$"}],
        }], "hasMore": False}
        mock_get.return_value = mock_response
        result = seats_aero.invoke({"action": "search", "origin_airport": "YVR", "destination_airport": "NRT",
                                    "include_trips": True})
        self.assertIn('"total_taxes": "98.10 CAD"', result)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "seats_aero")


def _recorded(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _response(payload=None, status=200, body_text=""):
    """A requests response, stood in for. A status of 400 or more makes raise_for_status raise."""
    import requests
    response = MagicMock()
    response.status_code = status
    if payload is None:
        response.json.side_effect = ValueError("not JSON")
        response.text = body_text
    else:
        response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status} Error", response=response)
    else:
        response.raise_for_status.return_value = None
    return response


@patch.dict(os.environ, {"SEATS_AERO_API_KEY": "test_api_key"})
class TestSeatsAeroData(unittest.TestCase):
    """cached_search and trips: seats.aero's responses as frozen dataclasses."""

    @patch("tools.seats_aero.requests.get")
    def test_cached_search_reads_a_recorded_response(self, mock_get):
        from tools.seats_aero import Availability, CabinAvailability, cached_search
        mock_get.return_value = _response(_recorded("search.json"))
        page = cached_search("sfo,oak", "MAD,BCN", start_date="2026-12-01", end_date="2026-12-07",
                             cabins="business", sources="iberia,united,aeroplan", order_by="lowest_mileage", take=1000)

        self.assertEqual(mock_get.call_args[0][0], "https://seats.aero/partnerapi/search")
        self.assertEqual(mock_get.call_args[1]["params"], {
            "origin_airport": "SFO,OAK", "destination_airport": "MAD,BCN", "take": 1000, "skip": 0,
            "start_date": "2026-12-01", "end_date": "2026-12-07", "cabins": "business",
            "sources": "iberia,united,aeroplan", "order_by": "lowest_mileage"})
        self.assertEqual((page.has_more, page.cursor, len(page.availability)), (False, 1761660130, 3))
        self.assertEqual(page.availability[0], Availability(
            id="2pYQ6Vx1sKfA9bLmN3cR7tWzE0u", origin="SFO", destination="MAD", date="2026-12-05", source="iberia",
            distance=5806,
            cabins=(CabinAvailability("economy", 34000, 7, True, "IB", 9840, "EUR", "€"),
                    CabinAvailability("business", 68000, 4, True, "IB", 21310, "EUR", "€"))))
        united, aeroplan = page.availability[1:]
        self.assertEqual([cabin.cabin for cabin in united.cabins], ["business"])  # only the cabins with seats
        self.assertEqual(united.cabin("business").taxes, "5.60 USD")
        self.assertIsNone(united.cabin("economy"))
        self.assertEqual((aeroplan.cabin("business").miles, aeroplan.cabin("business").seats), (70000, 0))
        self.assertEqual(page.availability[0].cabin("business").taxes, "213.10 EUR")

    @patch("tools.seats_aero.requests.get")
    def test_trips_reads_a_recorded_response(self, mock_get):
        from tools.seats_aero import BookingLink, Segment, trips
        mock_get.return_value = _response(_recorded("trips.json"))
        via_newark, via_frankfurt = trips("2pYQ7Kd3mWcB0nTqL5vX8rHsF2a")

        self.assertEqual(mock_get.call_args[0][0], "https://seats.aero/partnerapi/trips/2pYQ7Kd3mWcB0nTqL5vX8rHsF2a")
        self.assertEqual((via_newark.cabin, via_newark.miles, via_newark.stops, via_newark.seats), ("business", 88000, 1, 2))
        self.assertEqual((via_newark.taxes, via_newark.duration, via_newark.flight_numbers),
                         ("5.60 USD", "15h 55m", "UA1580, UA121"))
        self.assertEqual(via_newark.segments[0], Segment("UA1580", "SFO", "EWR", "2026-12-06T07:00:00Z",
                                                         "2026-12-06T15:29:00Z"))
        self.assertEqual((via_newark.connections, via_frankfurt.connections), (("EWR",), ("FRA",)))
        self.assertEqual(via_frankfurt.segments[1].carrier, "LH")
        links = (BookingLink("Book on United", "https://www.united.com/en/us/book-flight/united-awards", True),
                 BookingLink("Book on Air Canada", "https://www.aircanada.com/aeroplan/redeem", False))
        self.assertEqual((via_newark.booking_links, via_frankfurt.booking_links), (links, links))

    def test_connections_count_an_airport_change(self):
        from tools.seats_aero import Segment, Trip
        legs = (Segment("B6915", "SFO", "JFK", "", ""), Segment("IB6250", "LGA", "MAD", "", ""))
        trip = Trip(id="t", cabin="business", miles=1, taxes_cents=0, taxes_currency="", taxes_symbol="", stops=1,
                    carriers="", flight_numbers="", duration_min=0, departs_at="", arrives_at="", seats=1,
                    source="iberia", segments=legs)
        self.assertEqual(trip.connections, ("JFK", "LGA"))
        self.assertEqual(Trip(**{**trip.__dict__, "segments": legs[:1]}).connections, ())

    @patch("tools.seats_aero.requests.get")
    def test_errors_carry_the_http_status(self, mock_get):
        import requests
        from core.scheduler.preconditions.scan import is_network_error
        from tools.seats_aero import SeatsAeroError, cached_search
        for status, hiccup in ((401, False), (429, False), (503, True)):
            with self.subTest(status=status):
                mock_get.return_value = _response({"error": "no"}, status=status)
                with self.assertRaises(SeatsAeroError) as caught:
                    cached_search("SFO", "MAD")
                self.assertEqual(caught.exception.status, status)
                self.assertIsInstance(caught.exception.__cause__, requests.HTTPError)
                self.assertEqual(is_network_error(caught.exception), hiccup)
                self.assertIn(f'Response: {{"error": "no"}}', str(caught.exception))
        mock_get.side_effect = requests.Timeout("read timed out")
        with self.assertRaises(SeatsAeroError) as caught:
            cached_search("SFO", "MAD")
        self.assertIsNone(caught.exception.status)
        self.assertTrue(is_network_error(caught.exception))

    @patch("tools.seats_aero.requests.get")
    def test_a_gone_availability_is_a_404(self, mock_get):
        from tools.seats_aero import SeatsAeroError, trips
        mock_get.return_value = _response({}, status=404)
        with self.assertRaises(SeatsAeroError) as caught:
            trips("old_id")
        self.assertEqual((caught.exception.status, str(caught.exception)),
                         (404, "Availability object 'old_id' not found or has expired."))

    @patch("tools.seats_aero.requests.get")
    def test_no_key_raises_before_any_request(self, mock_get):
        from tools.seats_aero import NO_KEY, SeatsAeroError, cached_search
        with patch.dict(os.environ, {"SEATS_AERO_API_KEY": "", "SEATSAERO_API_KEY": ""}):
            with self.assertRaises(SeatsAeroError) as caught:
                cached_search("SFO", "MAD")
        self.assertEqual((str(caught.exception), caught.exception.status), (NO_KEY, None))
        mock_get.assert_not_called()

    @patch("tools.seats_aero.requests.get")
    def test_the_tool_formats_the_same_data(self, mock_get):
        mock_get.return_value = _response(_recorded("search.json"))
        reply = seats_aero.invoke({"action": "search", "origin_airport": "SFO", "destination_airport": "MAD"})
        payload = json.loads(reply.split("<payload>")[1].split("</payload>")[0])
        self.assertEqual((payload["count"], payload["has_more"], payload["cursor"]), (3, False, 1761660130))
        self.assertEqual(payload["results"][0], {
            "availability_id": "2pYQ6Vx1sKfA9bLmN3cR7tWzE0u", "route": "SFO -> MAD", "distance_miles": 5806,
            "source_program": "iberia", "date": "2026-12-05",
            "cabins": {"economy": {"mileage_cost": 34000, "remaining_seats": 7, "airlines": "IB", "direct": True},
                       "business": {"mileage_cost": 68000, "remaining_seats": 4, "airlines": "IB", "direct": True}}})

    @patch("tools.seats_aero.requests.get")
    def test_the_tool_words_errors_as_before(self, mock_get):
        mock_get.return_value = _response(None, status=503, body_text="<html>busy</html>")
        reply = seats_aero.invoke({"action": "search", "origin_airport": "SFO", "destination_airport": "MAD"})
        self.assertIn("<errors>Network or HTTP error communicating with Seats.aero: 503 Error Status: 503 Body: "
                      "<html>busy</html></errors>", reply)
        mock_get.return_value = _response({}, status=404)
        reply = seats_aero.invoke({"action": "trips", "availability_id": "old_id"})
        self.assertIn("<errors>Error: Availability object 'old_id' not found or has expired.</errors>", reply)


if __name__ == '__main__':
    unittest.main()
