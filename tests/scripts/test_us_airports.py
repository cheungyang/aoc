"""scripts/us_airports.py: the US airports list award_watch's us_only connections read."""
import csv
import io
import os
import tempfile
import unittest
from datetime import date

from scripts import us_airports as script

HEADER = ('"id","ident","type","name","latitude_deg","longitude_deg","elevation_ft","continent","iso_country",'
          '"iso_region","municipality","scheduled_service","icao_code","iata_code","gps_code","local_code",'
          '"home_link","wikipedia_link","keywords"')
ROWS = [
    # type, name, iso_country, scheduled_service, iata_code
    ("large_airport", "San Francisco International Airport", "US", "yes", "SFO"),
    ("medium_airport", "Luis Munoz Marin International Airport", "PR", "yes", "SJU"),
    ("medium_airport", "Antonio B. Won Pat International Airport", "GU", "yes", "GUM"),
    ("small_airport", "Ketchikan Harbor", "US", "yes", "KTN"),            # small, scheduled: kept
    ("small_airport", "A ranch strip", "US", "no", "XXR"),                # small, no service: left out
    ("medium_airport", "A field with no IATA code", "US", "no", ""),      # no code: left out
    ("heliport", "A heliport", "US", "yes", "JRA"),                       # not an airport type: left out
    ("seaplane_base", "A seaplane base", "US", "yes", "WSB"),             # not an airport type: left out
    ("large_airport", "London Heathrow Airport", "GB", "yes", "LHR"),     # not the US: left out
    ("large_airport", "Vancouver International Airport", "CA", "yes", "YVR"),
    ("medium_airport", "Pago Pago International Airport", "AS", "yes", "ppg"),  # case is set
    ("large_airport", "San Francisco International Airport (again)", "US", "yes", "SFO"),
]


def _csv():
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    for n, (kind, name, country, service, code) in enumerate(ROWS):
        writer.writerow([n, f"K{n}", kind, name, "0", "0", "0", "NA", country, f"{country}-X", "", service,
                         "", code, "", "", "", "", ""])
    return HEADER + "\n" + out.getvalue()


class TestUsAirportsScript(unittest.TestCase):

    def test_the_filter(self):
        self.assertEqual(script.us_codes(csv.DictReader(io.StringIO(_csv()))), ["GUM", "KTN", "PPG", "SFO", "SJU"])

    def test_the_file_has_a_header_then_one_code_a_line(self):
        text = script.render(["GUM", "SFO"], date(2026, 10, 28))
        lines = text.splitlines()
        self.assertEqual(lines[-2:], ["GUM", "SFO"])
        self.assertIn(f"Source: OurAirports (public domain), {script.SOURCE}, read 2026-10-28.", lines[1])
        self.assertIn("iso_country US, PR, GU, VI, AS, MP", lines[2])
        self.assertTrue(all(line.startswith("# ") for line in lines[:-2]))

    def test_main_reads_a_downloaded_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = os.path.join(tmp, "airports.csv"), os.path.join(tmp, "us_airports.txt")
            with open(source, "w", encoding="utf-8") as f:
                f.write(_csv())
            self.assertEqual(script.main(["--csv", source, "--out", out]), 0)
            with open(out, encoding="utf-8") as f:
                body = [line for line in f.read().splitlines() if not line.startswith("#")]
            self.assertEqual(body, ["GUM", "KTN", "PPG", "SFO", "SJU"])
            with open(source, "w", encoding="utf-8") as f:
                f.write("a,b\n1,2\n")  # not OurAirports' columns: nothing kept, nothing written
            self.assertEqual(script.main(["--csv", source, "--out", out + ".2"]), 1)
            self.assertFalse(os.path.exists(out + ".2"))
