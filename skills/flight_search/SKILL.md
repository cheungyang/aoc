---
name: flight_search
description: Search and optimize award flight routing, check live/cached seat inventory across airline alliances, drill down into flight segments, and calculate Cent-Per-Point (CPP) valuations using the Seats.aero tool.
---

## Core

Find and rank award flights with the `seats_aero` tool (its description covers every action and argument), balancing math with lifestyle: creative alliance redemptions, comfort over grueling itineraries.

### Guardrails
1. **Lifestyle Over Grind**: Never recommend itineraries with 15+ hour layovers or 3+ connections just to save a few thousand points. Always flag total travel duration, layover duration, and whether the itinerary is nonstop.
2. **Transfer Partner Accuracy**: Only recommend transfers from currencies the user actually holds in `pkm/wallet/credit-card/` (e.g. Amex MR, Chase UR, Capital One, Citi ThankYou, Bilt). Point out known transfer bonuses and program quirks (e.g. British Airways / Virgin high fuel surcharges vs. Air Canada / Avianca low cash surcharges).
3. **Valuation (CPP)**: Always evaluate CPP = (cash price − award taxes/fees) ÷ required points × 100. Flag redemptions below 1.3 CPP for Chase UR / Amex MR, and exceptional ones above 3.0 CPP for international Business/First.
4. **No Final Booking**: Provide the direct `booking_links` from `seats_aero` so the user can verify availability and complete the redemption on the airline portal.

### Workflow
1. **Read state**: use `filesystem` to read `pkm/wallet/travel/travel_preferences.md` (home airports, dream destinations, loyalty programs, cabin preferences) and the active cards and point balances in `pkm/wallet/credit-card/`.
2. **Search**: one `search` call covering every candidate airport and the whole date range (comma-separated codes, e.g. `SFO,OAK,SJC` to `HND,NRT`), with `cabins` set and `order_by="lowest_mileage"`.
3. **Drill down**: call `trips` for the 2-3 most promising availability IDs, in parallel, to verify exact flight numbers, layover times, aircraft product, and cash taxes. Treat availability older than 3 hours as stale: request a `refresh` and tell the user it may have changed.
4. **Recommend**, as a structured, multi-turn concierge recommendation:
   1. **Top Recommendation**: airline, cabin, flight numbers, departure/arrival times, total duration, nonstop status.
   2. **Point Cost & Transfer Strategy**: required points + exact cash taxes, and which card/bank currency to transfer from (e.g. *"Transfer 75,000 points from Amex MR to Air Canada Aeroplan"*).
   3. **Product Highlights**: aircraft type or cabin product (e.g. *ANA "The Room" 77W* or *Air France A350*).
   4. **Booking Link**: the direct booking link from the trip data.

Call `load_skill("flight_search")` when you need the program and alliance list (transfer partners, surcharges, sweet spots) or example calls.

## Reference

### Supported Mileage Programs & Alliances

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

### Example Calls

A specific trip inquiry (e.g. SFO to Tokyo in Business class for Nov 10-25):
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

Drill-down on a selected candidate:
```python
seats_aero(action="trips", availability_id="<selected_id>")
```

### Required Tools
- `seats_aero`: Required to perform cached searches, trip segment drilldown, nonstop destination discovery, and live inventory queries.
- `filesystem`: Required to read user wallet state (`pkm/wallet/`) and preferences.
