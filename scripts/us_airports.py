#!/usr/bin/env python3
"""Writes agents/reward-travel/lib/us_airports.txt: the US airports award_watch may connect at.

From OurAirports' airports.csv (public domain), it keeps each airport with an
IATA code in the US or its territories (PR, GU, VI, AS, MP) that is a large or
medium airport, or a small one with scheduled service. The file is one code a
line, sorted, under a header that names the source, the filter and the date.

    .venv/bin/python scripts/us_airports.py              # fetches the CSV and rewrites the file
    .venv/bin/python scripts/us_airports.py --csv FILE   # reads a CSV you downloaded instead
"""
import argparse
import csv
import io
import os
import sys
from datetime import date
from typing import Iterable, List, Mapping

SOURCE = "https://davidmegginson.github.io/ourairports-data/airports.csv"
# iso_country values kept: the US, Puerto Rico, Guam, the US Virgin Islands, American Samoa, the Northern Marianas.
COUNTRIES = ("US", "PR", "GU", "VI", "AS", "MP")
# Airport types kept whatever their service; a small_airport is kept only with scheduled service.
TYPES = ("large_airport", "medium_airport")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(project_root, "agents", "reward-travel", "lib", "us_airports.txt")


def us_codes(rows: Iterable[Mapping[str, str]]) -> List[str]:
    """The IATA codes the filter keeps from OurAirports' rows, sorted, each once."""
    codes = set()
    for row in rows:
        code = (row.get("iata_code") or "").strip().upper()
        if len(code) != 3 or not code.isalpha() or row.get("iso_country") not in COUNTRIES:
            continue
        kind = row.get("type")
        if kind in TYPES or (kind == "small_airport" and row.get("scheduled_service") == "yes"):
            codes.add(code)
    return sorted(codes)


def render(codes: List[str], day: date) -> str:
    """The file: its header, then one code a line."""
    header = [
        f"# US airport IATA codes, for award_watch's us_only connections. {len(codes)} codes.",
        f"# Source: OurAirports (public domain), {SOURCE}, read {day.isoformat()}.",
        f"# Filter: iso_country {', '.join(COUNTRIES)}; type large_airport or medium_airport, "
        "or small_airport with scheduled_service yes; an IATA code.",
        "# Made by scripts/us_airports.py: run it again to refresh this file.",
    ]
    return "\n".join(header + codes) + "\n"


def fetch() -> str:
    import requests

    resp = requests.get(SOURCE, timeout=60)
    resp.raise_for_status()
    return resp.content.decode("utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", help="read this airports.csv instead of fetching it")
    parser.add_argument("--out", default=OUT, help="where to write the codes")
    args = parser.parse_args(argv)
    if args.csv:
        with open(args.csv, encoding="utf-8") as f:
            text = f.read()
    else:
        text = fetch()
    codes = us_codes(csv.DictReader(io.StringIO(text)))
    if not codes:
        print("No US airport kept: has the CSV's format changed?", file=sys.stderr)
        return 1
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(codes, date.today()))
    print(f"Wrote {len(codes)} codes to {os.path.relpath(args.out, project_root)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
