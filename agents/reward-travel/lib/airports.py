"""US airports, for award_watch's "us_only" connections.

`is_us_airport(code)` reads us_airports.txt, next to this module: the IATA
codes of the airports in the US and its territories (PR, GU, VI, AS, MP),
from OurAirports. scripts/us_airports.py made it, and its header names the
source, the filter and the date; run that script again to refresh it.
"""
import os
from functools import lru_cache
from typing import FrozenSet

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_airports.txt")


@lru_cache(maxsize=1)
def us_airports() -> FrozenSet[str]:
    """The codes in us_airports.txt. Lines that start with # are its header."""
    with open(PATH, encoding="utf-8") as f:
        return frozenset(line.strip().upper() for line in f if line.strip() and not line.startswith("#"))


def is_us_airport(code: str) -> bool:
    """Whether `code` is a US airport's IATA code: SFO, SJU and GUM are; LHR isn't."""
    return (code or "").strip().upper() in us_airports()
