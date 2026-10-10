---
name: market_data
description: Pulls threshold financial data for stock tickers using the yfinance.py script.
---
## Overview
The `market_data` skill allows an agent to retrieve clean, structured financial data for stock tickers without needing unstructured web searches. It relies on a local Python script (`scripts/yfinance.py`) which uses the public `yfinance` library to fetch current prices, recent movements, and upcoming earnings dates.

## Workflow

### 1. Execute the Python Script
When you need to get financial data for stocks, execute the python script and pass one or more ticker symbols as arguments. This allows for highly efficient batch processing.

Command to run:
```bash
python scripts/yfinance.py <TICKER_1> <TICKER_2> ...
```
*Example: `python scripts/yfinance.py TSLA AAPL MSFT`*

### 2. Parse the Output
The script outputs a JSON array containing objects for each ticker:
- `symbol`: The ticker symbol.
- `current_price`: The latest closing or intraday price.
- `volume`: Today's volume.
- `daily_movement_pct`: The percentage change from the previous day's close.
- `next_earnings_date`: The upcoming earnings date (if available).
- `error`: Included only if data fetching failed for that specific ticker.

### 3. Threshold Evaluation (For Daily Monitors)
If you are running as a daily monitor, check the `daily_movement_pct` and `next_earnings_date` for each returned ticker.
- If `daily_movement_pct` is greater than +3.0% or less than -3.0%
- OR if the `next_earnings_date` is within the next 7 days
Then the stock has crossed the "Threshold Rule" and you should proceed with deeper web research for that specific ticker. Otherwise, the stock is considered "stable" and you can skip deep research to save tokens.

## Required Tools
- `yfinance`