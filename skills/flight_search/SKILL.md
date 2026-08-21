---
name: flight_search
description: Search and optimize award flight routing, check live/cached seat inventory across airline alliances, drill down into flight segments, and calculate Cent-Per-Point (CPP) valuations using the Seats.aero tool.
---

## Overview
This skill guides the agent in querying, analyzing, and optimizing award flight inventory across 20+ mileage programs and 70,000+ cached routes using the `seats_aero` tool. It incorporates the strategic philosophy of **Miles — The Strategic Travel Concierge**: balancing math with lifestyle, uncovering creative alliance redemptions, and prioritizing comfort over grueling itineraries.

---

## Boundaries & Guardrails

1. **Lifestyle Over Grind**:
   - Never recommend grueling itineraries with 15+ hour layovers or 3+ connections just to save a few thousand points.
   - Always flag total travel duration, layover duration, and whether an itinerary is nonstop.
2. **Transfer Partner Accuracy**:
   - Only recommend point transfers from currencies the user actually holds in `pkm/wallet/credit-card/` (e.g. Amex MR, Chase UR, Capital One, Citi ThankYou, Bilt).
   - Point out transfer bonuses if known, and highlight program-specific quirks (e.g. British Airways / Virgin high fuel surcharges vs. Air Canada / Avianca low cash surcharges).
3. **Valuation & CPP (Cent-Per-Point)**:
   - Always evaluate the Cent-Per-Point value:
     $$\text{CPP} = \frac{\text{Cash Price} - \text{Award Taxes/Fees}}{\text{Required Points}} \times 100$$
   - Flag redemptions below baseline thresholds (< 1.3 CPP for Chase UR / Amex MR) or exceptional redemptions (> 3.0 CPP for international Business/First).
4. **No Destructive Actions / Final Booking**:
   - Provide direct booking links (`booking_links`) from `seats_aero` so the user can verify availability and complete the redemption directly on the airline portal.

---

## Action Matrix & Tool Usage

Use the `seats_aero` tool with the appropriate `action`:

| Action | When to Use | Key Arguments |
| :--- | :--- | :--- |
| **`search`** / `cached_search` | Primary search for specific airport pairs and date ranges across all/selected programs. | `origin_airport`, `destination_airport`, `start_date`, `end_date`, `cabins`, `sources`, `only_direct_flights`, `order_by` |
| **`trips`** / `get_trips` | Detailed itinerary drill-down for an availability ID (exact flight numbers, layovers, duration, aircraft type, exact cash taxes, booking links). | `availability_id` (required), `include_filtered` |
| **`destinations`** / `get_destinations` | Quick nonstop destination discovery from (or to) an airport with cheapest award price per cabin. | `origin_airport` *OR* `destination_airport` |
| **`bulk_availability`** / `explore` | Broad regional search across entire continents for a single program (e.g. North America to Europe on Flying Blue). | `source` (required), `cabin`, `origin_region`, `destination_region`, `start_date`, `end_date` |
| **`routes`** / `get_routes` | Check which routes are tracked for a specific mileage program. | `source` (required) |
| **`refresh`** / `refresh_cache` | Request an async background refresh for stale availability items (>3 hours old). | `availability_ids` (list of IDs up to 250) |
| **`live_search`** / `live` | Live real-time scrape on an unmonitored city pair (commercial API key). | `origin_airport`, `destination_airport`, `departure_date`, `source`, `seat_count` |

---

## Supported Mileage Programs & Alliances

- **Star Alliance**:
  - `aeroplan` (Air Canada Aeroplan): Excellent partner chart, low taxes, transfers from Amex, Chase, Capital One, Bilt.
  - `lifemiles` (Avianca LifeMiles): No fuel surcharges, transfers from Amex, Capital One, Citi, Brex.
  - `united` (United MileagePlus): No fuel surcharges, transfers from Chase, Bilt.
  - `singapore` (Singapore KrisFlyer): Access to premium Singapore suites/business.
  - `turkish` (Turkish Miles & Smiles): High-value sweet spots, transfers from Capital One, Citi, Bilt.
  - `lufthansa` (Miles & More), `ethiopian` (ShebaMiles).
- **SkyTeam**:
  - `flyingblue` (Air France / KLM): Monthly Promo Rewards, great transatlantic business availability (Amex, Chase, Capital One, Citi, Bilt).
  - `virginatlantic` (Virgin Flying Club): ANA business/first sweet spots, SkyTeam partner awards (Amex, Chase, Capital One, Citi, Bilt).
  - `delta` (Delta SkyMiles): Amex MR transfer partner.
  - `eurobonus` (SAS), `saudia` (AlFursan), `aeromexico` (Club Premier).
- **Oneworld**:
  - `american` (American Airlines): Solid partner pricing (Qatar Qsuite, JAL).
  - `alaska` (Alaska Mileage Plan): Unified award chart, Starlux access (Bilt).
  - `qatar` (Qatar Privilege Club): Avios ecosystem, Qsuite redemptions (Amex, Citi, Capital One).
  - `finnair` (Finnair Plus), `qantas` (Qantas Frequent Flyer).
- **Independent / Direct**:
  - `emirates` (Skywards), `etihad` (Guest), `jetblue` (TrueBlue).

---

## Step-by-Step Concierge Workflow

### Step 1: Read State & Travel Preferences
1. Use `filesystem` to read `pkm/wallet/travel_preferences.md` (Home airports, Dream destinations, Loyalty programs, Cabin preferences).
2. Use `filesystem` to inspect active cards and point balances in `pkm/wallet/credit-card/`.

### Step 2: Query Award Availability
- For a specific trip inquiry (e.g. SFO to Tokyo in Business class for Nov 10-25):
  ```python
  seats_aero(
      action="search",
      origin_airport="SFO,OAK,SJC",
      destination_airport="HND,NRT",
      start_date="2024-11-10",
      end_date="2024-11-25",
      cabins="business",
      order_by="lowest_mileage"
  )
  ```

### Step 3: Drill Down on Top Candidates
- Select the 2-3 most promising availability IDs and call `trips` to verify exact flight numbers, layover times, aircraft product, and cash taxes:
  ```python
  seats_aero(action="trips", availability_id="<selected_id>")
  ```

### Step 4: Formulate Recommendations
Present a structured, multi-turn concierge recommendation to the user:
1. **Top Recommendation**: Airline, Cabin, Flight Numbers, Departure/Arrival times, Total Duration, Nonstop status.
2. **Point Cost & Transfer Strategy**: Required points + exact cash taxes, and which card/bank currency to transfer from (e.g. *"Transfer 75,000 points from Amex MR to Air Canada Aeroplan"*).
3. **Product Highlights**: Note aircraft type or cabin product (e.g. *ANA "The Room" 77W* or *Air France A350*).
4. **Booking Link**: Provide the direct booking link generated in the trip data.

---

## Required Tools
- `seats_aero`: Required to perform cached searches, trip segment drilldown, nonstop destination discovery, and live inventory queries.
- `filesystem`: Required to read user wallet state (`pkm/wallet/`) and preferences.
