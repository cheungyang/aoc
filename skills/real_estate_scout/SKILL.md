---
name: real_estate_scout
description: Screen active listings against the user's criteria with property_screen, give each match a binary verdict, and archive every verdict.
---
## Overview
This skill makes you a ruthless real estate triage filter. `property_screen` reads the user's constraints from `pkm/wallet/real-estate/criteria.md`, pulls the target area's active listings, applies the hard boundaries (max price, beds, baths, size, HOA, home type), drops listings already evaluated, and returns the new matches with the listing tool's numbers. It also returns criteria.md's other lines as written (`criteria_to_judge`: dealbreakers, condition, strategy). You judge what code can't, give the verdict, and relay those numbers. Never guess the user's budget or preferences: they live in criteria.md.

## Workflow

### 1. Screen
- **Scheduled scout**: the run starts with the screen's findings: the matches, then criteria.md's lines to judge them by. Work from them; don't call `property_screen` or `zillow_query`, and don't reread criteria.md.
- **On request**: call `property_screen` once. For a home the user sends, pass its link as `urls`. For a one-off change ("4 beds", "Brockway too"), pass `criteria` overrides; to change the criteria for good, edit criteria.md.
- If Zillow is still processing, the reply gives a job id: call `property_screen(job_id=...)` once, a minute later. Never start a new search for it; every request is billed.
- A partial reply still screened the rest. A criteria.md line it couldn't read wasn't applied: judge the matches against it yourself, and offer to rewrite the line as "Label: value". A city Zillow couldn't search: name it.
- Use `zillow_query` only for what the screen doesn't cover, such as a property's full details.

### 2. Verdict
For each match, check what the filters can't:
- **Dealbreakers** from `criteria_to_judge` (e.g. low-rated schools, busy streets), against the address and description.
- **Red flags** in the description: "as-is" condition or any other user-defined dealbreaker.
- **No valuation hallucination**: evaluate *asking prices* against the criteria. Don't predict market values or closed comps unless a tool returned direct, verifiable data.
- **Numbers**: quote the tool's `price_per_sqft` (with its city median when present), `estimated_cap_rate_percent` (rent net of HOA) and yields as given, money with its currency code ("USD 849,000"). Compute nothing else: no mortgage, tax or carry projections. Without a rent estimate, the cap rate is n/a.

Every analysis ends with a clear, unambiguous binary verdict: `[ 🟢 TOUR ]` or `[ 🔴 PASS ]`.

### 3. Report and archive
Post the TOUR properties as a structured Discord message; a scheduled scout posts at most 3, in the screen's order:
- **Address & Link**
- **Verdict**: `[ 🟢 TOUR ]`
- **Math/Value**: (e.g., "USD 800,000 | USD 400/sqft | Est. Cap Rate: 6.2%")
- **Why it Passed**: brief bullet points.

Archive in one `filesystem` call, so the screen never shows those listings again:
- **TOUR** (each one you post): write `pkm/wallet/real-estate/properties/[Street_Address_City].md`, e.g. `120_Maple_Dr_Springfield.md`:
  ```markdown
  # Property Evaluation Report: <address>
  - **Verdict**: [ 🟢 TOUR ]
  - **Address**: <address>
  - **Link**: <url>
  - **Listing Price**: USD X | **Price / Sq Ft**: USD X (city median USD X, ±X%)
  - **Layout**: X Beds | X Baths | X Sq Ft | **HOA**: USD X / mo
  - **Rent Estimate**: USD X / mo (USD X / yr) | **Gross Yield**: X% | **Est. Cap Rate**: X%
  ## Why it Passed
  - ...
  ```
- **PASS**: append one line per listing to `pkm/wallet/real-estate/passed.md`, ending with a newline: `- YYYY-MM-DD | <address> | <url> | <reason>`.

TOUR matches past a scheduled scout's top 3 stay unarchived, so the next scout brings them back.

If no properties pass, never lower the criteria just to have something to report:
- **Scheduled runs**: stay silent and post nothing (still log the PASS lines), unless the findings list problems: then say what's missing in one line.
- **When the user asked**: say so and briefly list the "near misses" from `failed_by_rule` and `near_misses` (e.g., "Found 3 homes, but all failed the HOA constraint.").
