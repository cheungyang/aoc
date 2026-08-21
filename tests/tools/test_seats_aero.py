import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.seats_aero import seats_aero
from core.util import format_tool_response


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
        self.assertIn("$56.20", result)
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


if __name__ == '__main__':
    unittest.main()
