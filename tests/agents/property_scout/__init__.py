"""Tests for agents/property-scout, one module per module.

Shared fixtures: a made-up criteria file and TOUR report in the shape of the
pkm/wallet/real-estate files, a Zillow listing shaped like the API's (and
like tests/tools/test_zillow_query.py's), and a fake of the RapidAPI job
endpoints that counts every request.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.config.settings import Config

CRITERIA_MD = """# Home Criteria

## 1. The Home
- **Target Corridor**: Springfield metro, east side (Springfield to Capital City)
  - Key Target Cities: Springfield, Shelbyville, Ogdenville, North Haverbrook, Capital City
- **Max Purchase Price**: $1,000,000 (Hard Ceiling)
- **Minimum Layout**: 3 Beds, 2 Baths, ≥ 1,400 Sq Ft (Preferred: 4 Beds, 2.5 Baths)
- **Property Type**: Single Family Home (1 or 2 stories)
- **Max HOA**: $0 / No HOA
- **Dealbreakers**:
  - Low-rated schools
  - Busy streets or highway noise

## 2. Money
- **Financing**: 20% down payment (~$180k on $900k).
"""

# The file plus the one line the scan needs to search by city.
CRITERIA_WITH_STATE = CRITERIA_MD.replace(
    "- **Max Purchase Price**", "- **State**: IL\n- **Max Purchase Price**"
)

REPORT_MD = """# Property Evaluation Report: 120 Maple Dr, Springfield, IL 62701

## Executive Summary
- **Verdict**: [ 🟢 TOUR ]
- **Address**: 120 Maple Dr, Springfield, IL 62701
- **Listing Price**: $849,000
- **Price / Sq Ft**: $597.89
"""


def listing(zpid, street, city="Springfield", price=849_000, beds=3, baths=2.0, sqft=1420, hoa=None,
            rent=3650, home_type="SINGLE_FAMILY", zipcode="62701", description="Updated kitchen."):
    return {
        "zpid": zpid,
        "url": f"https://www.zillow.com/homedetails/{zpid}_zpid/",
        "street_address": street,
        "city": city,
        "state": "IL",
        "zipcode": zipcode,
        "price": price,
        "bedrooms": beds,
        "bathrooms": baths,
        "living_area": sqft,
        "hoa_fee": hoa,
        "rent_zestimate": rent,
        "home_type": home_type,
        "description": description,
    }


def enriched(*args, **kwargs):
    """A listing as zillow_query returns it, with price/sqft and the cap-rate estimates."""
    from tools.zillow_query import _enrich_property
    return _enrich_property(listing(*args, **kwargs))


class FakeZillow:
    """The RapidAPI job endpoints: POST /properties submits, GET /results/<id> reads.

    `statuses` is what each read reports, the last one repeating; `results`
    are the listings a complete job returns. Every request is recorded.
    """

    def __init__(self, results=(), statuses=("complete",), errors=None, status_code=200):
        self.results = list(results)
        self.statuses = list(statuses)
        self.errors = errors
        self.status_code = status_code
        self.posts, self.gets, self.sleeps = [], [], []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self._response({"job_id": "job-1", "status": "processing", "results": [], "errors": []})

    def get(self, url, headers=None, timeout=None):
        self.gets.append(url)
        status = self.statuses[min(len(self.gets), len(self.statuses)) - 1]
        body = {"job_id": "job-1", "status": status, "errors": self.errors or []}
        body["results"] = [{"success": True, "property": p} for p in self.results] if status == "complete" else []
        return self._response(body)

    def _response(self, body):
        resp = MagicMock()
        resp.status_code = self.status_code
        resp.json.return_value = body
        if self.status_code >= 400:
            import requests
            side = "Client" if self.status_code < 500 else "Server"
            error = requests.HTTPError(f"{self.status_code} {side} Error")
            error.response = resp
            resp.raise_for_status.side_effect = error
        else:
            resp.raise_for_status.return_value = None
        return resp

    @property
    def requests(self):
        return len(self.posts) + len(self.gets)

    def install(self, case: unittest.TestCase):
        for target, fake in (
            ("agents.property_scout.lib.scout.requests.post", self.post),
            ("agents.property_scout.lib.scout.requests.get", self.get),
            ("agents.property_scout.lib.scout.time.sleep", self.sleeps.append),
        ):
            patcher = patch(target, side_effect=fake)
            patcher.start()
            case.addCleanup(patcher.stop)
        return self


class PkmCase(unittest.TestCase):
    """A temp pkm with pkm/wallet/real-estate/, and a RapidAPI key."""

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        config = Config()
        config.pkm_dir = self.pkm
        config.rapidapi_key = "test-key"
        self.addCleanup(self._cleanup)
        self.base = os.path.join(self.pkm, "wallet", "real-estate")
        os.makedirs(os.path.join(self.base, "properties"))

    def _cleanup(self):
        config = Config()
        config.pkm_dir = None
        config.rapidapi_key = None
        shutil.rmtree(self.pkm, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path
