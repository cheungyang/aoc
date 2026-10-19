# property-scout: how its tools work

`property_screen` (the tool) and the weekly scan of the same name run one screen: read criteria.md, search Zillow once for the whole target area, apply the hard filters, and drop listings already evaluated. Rio gives every verdict; nothing here judges a listing.

property-scout has no RULES.md: its filters are the user's own criteria.md.

## criteria.md

The filters live in `pkm/wallet/real-estate/criteria.md`, written for people. Every line is one of four kinds:

- **A filter line**: "Label: value", where the label names a filter (the table below). The first line for a filter wins; a later line that sets it to another value is reported (partial).
- **A filter line that doesn't read**: the label names a filter but the value doesn't read ("Max HOA: ask me"). It comes back raw in the issues (partial), and that filter isn't applied.
- **Structure**: blank lines, headings and rules.
- **Everything else**: dealbreakers, condition, strategy, any label that names no filter. These come back as written in `criteria_to_judge`, for Rio's verdict.

| Label (examples) | Filter it sets |
|---|---|
| Max Purchase Price, Budget | `max_price`: the first amount written with `$` or `USD` ("$1M", "USD 1,000,000") |
| Minimum Layout | `min_beds`, `min_baths`, `min_sqft`; after "Preferred": `preferred_beds`, `preferred_baths` |
| Min Beds, Min Baths, Min Sq Ft | the first number |
| Max HOA | `max_hoa`, a monthly amount; 0 for "none" or "No HOA" |
| Property Type, Home Type | `home_types` and `max_stories` |
| Key Target Cities | `cities`, comma-separated |
| Target Zip Codes | `zipcodes`, searched instead of the cities |
| State | `state`, a two-letter code |
| Min Cap Rate | `min_cap_rate`, a percentage |

Other "Max ..." labels ("Max $/SqFt") and lot sizes name no filter, so they are for judgment. The preferred layout is a sort key, never a filter.

A file with cities, a state, a price, an HOA line that doesn't read and a dealbreaker:

```python
>>> from agents.property_scout.lib.criteria import Criteria, filter_values, parse_criteria, with_overrides
>>> parsed = parse_criteria("\n".join([
...     "## 1. The Home",
...     "- **Key Target Cities**: Springfield, Shelbyville",
...     "- **State**: IL",
...     "- **Max Purchase Price**: $1,000,000 (Hard Ceiling)",
...     "- **Max HOA**: ask me",
...     "- **Dealbreakers**:",
...     "  - Busy streets",
... ]))
>>> parsed.criteria.cities, parsed.criteria.state, parsed.criteria.max_price
(('Springfield', 'Shelbyville'), 'IL', 1000000.0)
>>> parsed.unread
('- **Max HOA**: ask me',)
>>> parsed.to_judge
('- **Dealbreakers**:', '  - Busy streets')
```

Layout, type and HOA lines, and a "Max" label that names no filter:

```python
>>> filter_values("- **Minimum Layout**: 3 Beds, 2 Baths, ≥ 1,400 Sq Ft (Preferred: 4 Beds, 2.5 Baths)")
{'min_beds': 3.0, 'min_baths': 2.0, 'min_sqft': 1400.0, 'preferred_beds': 4.0, 'preferred_baths': 2.5}
>>> filter_values("- **Property Type**: Single Family Home (1 or 2 stories)")
{'home_types': ('SINGLE_FAMILY',), 'max_stories': 2}
>>> filter_values("- **Max HOA**: $0 / No HOA")
{'max_hoa': 0.0}
>>> filter_values("- **Max $/SqFt**: $1,200") is None
True
```

A second line for the same filter with another value: the first applies, and the issues say so.

```python
>>> parse_criteria("- Max Price: $1,000,000\n- Budget: $2M").conflicts
("line 2 ('- Budget: $2M') sets max_price again; line 1's value applies",)
```

What's required (status failed, no search): the file itself, and somewhere to search, meaning zip codes, or cities with a state. A check of listings the user sent (`urls`) or of a job already submitted (`job_id`) needs no search area.

```python
>>> Criteria(cities=("Shelbyville",)).area_problem()
"names no state for its target cities: add a line like '- **State**: CA', or list zip codes"
>>> Criteria(cities=("Shelbyville",), zipcodes=("62702",)).search_areas()
['62702']
```

The `criteria` argument overrides filters for one call. Naming cities searches them instead of the file's zip codes, and money may be written as the tool writes it:

```python
>>> c = with_overrides(Criteria(zipcodes=("62701",), state="IL", max_price=1_000_000),
...                    {"cities": "Brockway", "max_price": "USD 1,200,000"})
>>> c.search_areas(), c.max_price
(['Brockway, IL'], 1200000.0)
```

## property_screen

### One search, and the request budget

One Zillow job covers every target area: a search URL per zip code, else per "City, ST", capped at `max_price`.

```python
>>> from agents.property_scout.lib.scout import SCAN_POLLING, TOOL_POLLING, search_urls
>>> search_urls(Criteria(cities=("Springfield", "Capital City"), state="IL", max_price=1_000_000))
['https://www.zillow.com/homes/for_sale/Springfield-IL/0-1000000_price/', 'https://www.zillow.com/homes/for_sale/Capital-City-IL/0-1000000_price/']
```

Zillow bills every request, the result reads included, so polling waits before its first read and reads sparsely:

- A call on request: one submit and at most 9 reads, 10 requests. A job still running after that comes back failed with its job id; `job_id` then reads it once, without a new search.
- The weekly scan: at most 9 requests a week, one submit and 8 reads. This is a hard limit, kept in code. A job still running after that is a timeout: logged, not posted, and next week's run searches again.

```python
>>> SCAN_POLLING.requests, SCAN_POLLING.read_times()
(9, [15, 30, 45, 60, 75, 90, 105, 120])
>>> TOOL_POLLING.requests, TOOL_POLLING.read_times()
(10, [8, 14, 20, 26, 32, 38, 44, 50, 56])
```

Zillow's errors for some search URLs are issues (partial): the answer covers the other areas. No listings back with errors, or a failed job, is failed. No listings and no errors is a dry search (ok).

### The listing tool's numbers

The shared `zillow_query` code enriches each listing, and the screen copies its numbers as they are:

- price per sqft = price ÷ living area;
- the estimated cap rate = (monthly rent estimate − monthly HOA) × 12 ÷ price, a missing HOA counting as 0;
- the gross yield = rent estimate × 12 ÷ price.

Without a rent estimate there is no cap rate or yield: they show n/a, never a guess such as the 1% rule. 120 Maple Dr, at USD 849,000 for 1,420 sqft, with rent at USD 3,650 a month and no HOA:

```python
>>> from tools.zillow_query import _enrich_property
>>> house = _enrich_property({"price": 849_000, "living_area": 1420, "rent_zestimate": 3650, "hoa_fee": None})
>>> house["price_per_sqft"], house["estimated_annual_gross_rent"], house["estimated_cap_rate_percent"]
(597.89, 43800.0, 5.16)
>>> _enrich_property({"price": 849_000, "living_area": 1420, "rent_zestimate": None})["estimated_cap_rate_percent"] is None
True
```

### The filters

Each filter checks one fact, and its boundary passes (exactly the max price, exactly the minimum beds). A missing price, bed or bath count, or size fails its filter, since it can't be verified. A missing HOA, home type or story count passes, since Zillow often leaves them out (a missing HOA usually means none). Each failure names the listing's value.

```python
>>> from agents.property_scout.lib.listings import misses, render_matches, screen
>>> rules = Criteria(max_price=1_000_000, min_beds=3, max_hoa=0, home_types=("SINGLE_FAMILY",))
>>> misses({"price": 1_000_000, "beds": 3, "hoa_fee": None, "home_type": None}, rules)
[]
>>> misses({"price": 1_080_000, "beds": None, "hoa_fee": 350, "home_type": "CONDO"}, rules)
[('over USD 1,000,000', 'USD 1,080,000'), ('beds not listed', ''), ('HOA over USD 0/mo', 'USD 350/mo'), ('not single family', 'condo')]
```

### Order, city median and near misses

- Matches come preferred layout first, then lowest $/sqft. A scheduled scout posts at most 3 TOURs, in this order.
- A city's median $/sqft is taken over every listing pulled in that city, rejected ones included, once there are at least 3. `vs_area_median_pct` is how far a listing sits from it.
- `failed_by_rule` counts the listings each filter rejected. `near_misses` holds up to 5 listings that failed exactly one filter.
- Listings already evaluated drop out (see the archive); `already_evaluated` counts them.

Four Shelbyville homes at USD 400 to 520 a sqft, so the city median is USD 460. The 4-bed meets the preferred layout and leads, and the USD 1,040,000 home misses only the price:

```python
>>> def home(zpid, price, sqft, beds=3):
...     return _enrich_property({"zpid": zpid, "street_address": f"{zpid} Main St", "city": "Shelbyville", "state": "IL",
...                              "price": price, "bedrooms": beds, "bathrooms": 2, "living_area": sqft,
...                              "rent_zestimate": 2400})
>>> result = screen([home(1, 800_000, 2000), home(2, 960_000, 2000, beds=4), home(3, 880_000, 2000),
...                  home(4, 1_040_000, 2000)], Criteria(max_price=1_000_000, min_beds=3, preferred_beds=4))
>>> [(row["zpid"], row["price_per_sqft"], row["vs_area_median_pct"]) for row in result.matches]
[(2, 480.0, 4.3), (1, 400.0, -13.0), (3, 440.0, -4.3)]
>>> result.failed_by_rule, [(near["address"], near["missed"]) for near in result.near_misses]
({'over USD 1,000,000': 1}, [('4 Main St, Shelbyville, IL', 'over USD 1,000,000 (USD 1,040,000)')])
```

### What comes back

Money carries its currency code: "USD 849,000", and a price per sqft keeps its cents. A missing amount stays null ("none listed" for an HOA).

```python
>>> from agents.property_scout.lib.money import with_currency
>>> with_currency({"price": 849_000, "price_per_sqft": 597.89, "hoa_fee": None, "sqft": 1420})
{'price': 'USD 849,000', 'price_per_sqft': 'USD 597.89', 'hoa_fee': None, 'sqft': 1420}
```

The JSON holds `criteria` (the filters applied), `pulled`, `matches`, `already_evaluated`, `failed_by_rule`, `near_misses` and `criteria_to_judge`. With `urls` it holds `listings` instead: every listing sent, each with `missed`. With `explain=true` the envelope also lists the files and the Zillow job read, the line each filter came from, each match's inputs, examples of each filter's rejections, and the archive entry behind each listing already evaluated.

The tool leaves out the verdict, the dealbreakers and every valuation beyond these numbers.

## The archive

A listing counts as evaluated when its zpid (from a Zillow link) or its street and city appear in either record. Streets are compared in short form ("Drive" and "Dr" match):

- a TOUR report, `pkm/wallet/real-estate/properties/<Street_Address_City>.md`: its file name, its `**Address**` lines and title, and the zpids in its links;
- a PASS line in `pkm/wallet/real-estate/passed.md`: `- YYYY-MM-DD | <address> | <url> | <reason>`.

A passed.md line that is none of a PASS line, a heading or blank comes back raw (partial), and the listing it names isn't counted as evaluated.

```python
>>> from agents.property_scout.lib.archive import Archive, address_key
>>> address_key("120 Maple Drive, Springfield, IL 62701")
'120mapledrspringfield'
>>> archive = Archive()
>>> archive.add_report("120_Maple_Dr_Springfield.md", "- **Link**: https://www.zillow.com/homedetails/10000001_zpid/")
>>> archive.add_pass_log("# Passed\n- 2026-10-16 | 123 Main St, Shelbyville, IL 62702 | no link | busy road\nnot a PASS line")
(1, ['not a PASS line'])
>>> archive.find({"address": "120 Maple Dr, Springfield, IL 62701"})
'properties/120_Maple_Dr_Springfield.md'
>>> archive.find({"address": "", "zpid": 10000001})
'properties/120_Maple_Dr_Springfield.md'
>>> archive.find({"address": "123 Main Street, Shelbyville, IL 62702"})
'passed.md line 2'
>>> archive.find({"address": "9 Oak Ct, Ogdenville, IL 62703"}) is None
True
```

## The weekly scan

The `property_screen` scan runs the same screen before the weekly scout, within its 9 requests. With no new match it skips the run, and the model isn't called. With new matches it wakes Rio with these findings:

- a header: how many are new, how many were pulled, how many matches were already evaluated;
- each match as a block, as below;
- criteria.md's filter lines that didn't read, if any;
- criteria.md's lines for judgment.

```python
>>> print(render_matches(result.matches[:1]))
1. 2 Main St, Shelbyville, IL
   https://www.zillow.com/homedetails/2_zpid/
   USD 960,000 | 4 bd / 2 ba | 2,000 sqft | USD 480.00/sqft (city median USD 460.00, +4.3%) | HOA none listed | preferred layout
   Rent est. USD 2,400/mo (USD 28,800/yr) | gross yield 3% | est. cap rate 3%
```

Problems follow the scan rules. Issues on a week with matches ride along in the findings. Issues on a dry week are posted in #real-estate, once a day per issue. No criteria file, nowhere to search, or no listings back skip the run and are posted. Network errors and a job still running are only logged.
