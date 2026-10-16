---
name: real_estate_scout
description: Guides the agent to proactively query active real estate listings, evaluate them against strict criteria, and generate Markdown property reports.
---
## Overview
This skill instructs the agent on how to function as a ruthless real estate triage filter. It covers how to read user preferences, run strict financial evaluations (Cap Rates, Price/SqFt), and output highly structured verdicts.

## Workflow

### 1. Ingest Criteria
Always begin by using the `filesystem` tool to read the user's constraints from `pkm/wallet/real-estate/criteria.md`. Never guess their budget or preferences. Note all hard boundaries (max price, target zip codes, max HOA, min Cap Rate).

### 2. Query the Market
Use the `zillow_query` tool to search for active property listings based on the zip codes and baseline constraints found in Step 1.

### 3. Mathematical Evaluation (The Triage)
For each property returned by the API:
- **Price/SqFt**: Use the tool's `price_per_sqft` rather than recomputing it. Compare against the user's expected baseline or neighborhood average.
- **Investment Math**: If operating in investment mode, use the tool's `estimated_cap_rate_percent` (from its rent estimate, net of HOA). If there is no rent estimate, estimate monthly rent with the 1% rule if applicable, calculate the Cap Rate, and label it as an estimate.
- **Red Flag Check**: Scan listing descriptions and variables for high HOAs, "as-is" condition, or other user-defined dealbreakers.
- **No Valuation Hallucination**: Evaluate *asking prices* against the user's criteria. Do NOT predict exact market values or closed comps unless a tool returned direct, verifiable data.

### 4. Output Verdict & Report
Every analysis must end with a clear, unambiguous binary verdict: `[ 🟢 TOUR ]` or `[ 🔴 PASS ]`. For the properties that survive the triage filter, generate a structured Discord message:
- **Address & Link**
- **Verdict**: `[ 🟢 TOUR ]`
- **Math/Value**: (e.g., "$800k | $400/sqft | Est. Cap Rate: 6.2%")
- **Why it Passed**: Brief bullet points.

For any property receiving a `[ 🟢 TOUR ]` verdict, you MUST use the `filesystem` tool to write a structured Markdown report to `pkm/wallet/real-estate/properties/[Formatted_Address].md`.

If no properties pass, never lower the criteria just to have something to report:
- **Scheduled runs**: stay silent and post nothing.
- **When the user asked**: say so and briefly list the "near misses" (e.g., "Found 3 homes, but all failed the HOA constraint.").

## Required Tools
- `filesystem`: Required to read `criteria.md` and write markdown reports to the `properties/` directory within `pkm/wallet/real-estate/`.
- `zillow_query`: Required to pull structured, active listing data.
