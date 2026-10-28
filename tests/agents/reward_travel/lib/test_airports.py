"""agents/reward-travel/lib/airports.py: which airports are in the US, for us_only connections."""
import unittest

from agents.reward_travel.lib.airports import PATH, is_us_airport, us_airports


class TestUsAirports(unittest.TestCase):

    def test_us_airports_and_territories_are_us(self):
        for code in ("SFO", "JFK", "EWR", "ORD", "HNL", "ANC", "SJU", "GUM", "STT", "PPG", "SPN", "sfo", " sju "):
            with self.subTest(code=code):
                self.assertTrue(is_us_airport(code))

    def test_other_airports_arent(self):
        for code in ("LHR", "MAD", "YVR", "MEX", "NRT", "", "SF"):
            with self.subTest(code=code):
                self.assertFalse(is_us_airport(code))

    def test_the_file_loads(self):
        codes = us_airports()
        self.assertGreater(len(codes), 900)
        self.assertTrue(all(len(code) == 3 and code.isalpha() and code.isupper() for code in codes))
        with open(PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
        header = [line for line in lines if line.startswith("#")]
        self.assertIn("OurAirports", header[1])
        self.assertIn("read 20", header[1])
        body = lines[len(header):]
        self.assertEqual(body, sorted(set(body)))  # sorted, each once, the header first
        self.assertEqual(len(body), len(codes))
