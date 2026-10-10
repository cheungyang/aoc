import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Union

# Aliased on purpose: the tool function below must be named `yfinance` (the loader
# resolves tools/<name>.py -> <name>), and would otherwise shadow the library.
import yfinance as yf
from langchain_core.tools import tool

from core.runtime.execution_context import try_context
from core.util import format_tool_response


TOOL_NAME = "yfinance"
MAX_TICKERS = 50
MAX_WORKERS = 8
MAX_HISTORY_BARS = 65
VALID_PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
# Sessions averaged (excluding the latest one) for avg_volume / volume_ratio.
AVG_VOLUME_SESSIONS = 20

# yfinance prints "possibly delisted" etc. straight to its logger; those are surfaced
# per ticker in the payload instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


@tool
def yfinance(
    action: str = "quote",
    tickers: Optional[List[str]] = None,
    period: str = "1mo",
) -> str:
    """
    Fetch public stock market data from Yahoo Finance for one or more ticker symbols in a single batch call.

    Supported Actions:
    - 'quote' (default): Snapshot per ticker for screening.
        Returns: symbol, currency, current_price, previous_close, daily_movement_pct, volume,
        avg_volume (mean of the prior 20 sessions), volume_ratio (volume / avg_volume),
        next_earnings_date (YYYY-MM-DD or null), days_to_earnings (int or null), as_of_date.
    - 'history': Daily bars for the requested period.
        Returns: symbol, period, period_change_pct, period_high, period_low, bars_truncated,
        bars: [{date, close, volume, change_pct}] (at most the latest 65 bars).
    - 'earnings': Upcoming and recent earnings.
        Returns: symbol, next_earnings_date, days_to_earnings,
        recent: [{date, eps_estimate, eps_reported, surprise_pct}] (up to 4 most recent reported).

    Args:
        action: 'quote', 'history', or 'earnings'. Defaults to 'quote'.
        tickers: List of ticker symbols, e.g. ["AAPL", "TSLA", "MSFT"]. Case-insensitive,
            de-duplicated, max 50 per call. Pass ALL tickers in one call rather than looping.
        period: Only used by 'history'. One of 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max.
            Defaults to '1mo'.

    Output notes:
        - All *_pct fields are percentages (3.2 means +3.2%), rounded to 2 decimals.
        - Payload is JSON: {"action", "as_of", "count", "results": [...]}, results in input order.
        - Failures are reported per ticker via an "error" field; other tickers still return data.
        - Prices may be delayed and reflect the latest session (intraday during market hours).
    """
    ctx = try_context()
    if ctx is not None:
        from core.loaders.tools_loader import ToolsLoader
        if not ToolsLoader().check_permission(ctx, TOOL_NAME, action):
            return format_tool_response(
                TOOL_NAME,
                payload="",
                errors=f"Error: Agent '{ctx.agent_id}' does not have permission to execute action '{action}' on {TOOL_NAME}.",
            )

    symbols = _normalize_tickers(tickers)
    if not symbols:
        return format_tool_response(TOOL_NAME, payload="", errors="Error: 'tickers' must be a non-empty list of symbols, e.g. [\"AAPL\", \"TSLA\"].")
    if len(symbols) > MAX_TICKERS:
        return format_tool_response(TOOL_NAME, payload="", errors=f"Error: At most {MAX_TICKERS} tickers per call (got {len(symbols)}).")

    norm_action = (action or "quote").lower().strip()
    if norm_action == "quote":
        worker = _quote
    elif norm_action == "history":
        period = (period or "1mo").lower().strip()
        if period not in VALID_PERIODS:
            return format_tool_response(TOOL_NAME, payload="", errors=f"Error: Invalid period '{period}'. Valid: {', '.join(VALID_PERIODS)}.")
        worker = lambda sym: _history(sym, period)  # noqa: E731
    elif norm_action == "earnings":
        worker = _earnings
    else:
        return format_tool_response(TOOL_NAME, payload="", errors=f"Error: Unknown action '{action}'. Supported actions: 'quote', 'history', 'earnings'.")

    try:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(symbols))) as pool:
            results = list(pool.map(lambda s: _safe(worker, s), symbols))
    except Exception as e:
        return format_tool_response(TOOL_NAME, payload="", errors=f"Error in {TOOL_NAME}: {e}")

    payload = {
        "action": norm_action,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(results),
        "results": results,
    }
    failed = [r["symbol"] for r in results if "error" in r]
    errors = "None" if not failed else f"Data unavailable for: {', '.join(failed)}"
    return format_tool_response(TOOL_NAME, payload=json.dumps(payload, indent=2), errors=errors)


# --------------------------------------------------------------------------- helpers

def _normalize_tickers(tickers: Optional[Union[List[str], str]]) -> List[str]:
    """Upper-cases, strips and de-duplicates (order preserved). Tolerates a delimited string."""
    if tickers is None:
        return []
    if isinstance(tickers, str):
        tickers = tickers.replace(",", " ").split()
    seen, out = set(), []
    for t in tickers:
        sym = str(t).strip().upper().lstrip("$")
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _safe(worker, symbol: str) -> Dict[str, Any]:
    try:
        return worker(symbol)
    except Exception as e:
        return {"symbol": symbol, "error": f"{type(e).__name__}: {e}"}


def _num(value: Any, digits: int = 2) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 2)


def _to_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):  # pandas Timestamp
        try:
            return value.date()
        except Exception:
            return None
    return None


def _next_earnings(ticker: "yf.Ticker") -> Optional[date]:
    """Earliest earnings date >= today from Ticker.calendar, or None."""
    try:
        cal = ticker.calendar
    except Exception:
        return None
    raw = None
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
    elif cal is not None and hasattr(cal, "loc"):  # older yfinance returned a DataFrame
        try:
            raw = list(cal.loc["Earnings Date"].values)
        except Exception:
            raw = None
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    today = date.today()
    upcoming = sorted(d for d in (_to_date(x) for x in raw) if d is not None and d >= today)
    return upcoming[0] if upcoming else None


def _days_until(d: Optional[date]) -> Optional[int]:
    return (d - date.today()).days if d else None


def _load_history(ticker: "yf.Ticker", period: str):
    hist = ticker.history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty or "Close" not in hist:
        raise ValueError("No price data returned (invalid or delisted symbol?)")
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        raise ValueError("No price data returned (invalid or delisted symbol?)")
    return hist


# --------------------------------------------------------------------------- actions

def _quote(symbol: str) -> Dict[str, Any]:
    ticker = yf.Ticker(symbol)
    hist = _load_history(ticker, "3mo")

    closes = hist["Close"]
    volumes = hist["Volume"] if "Volume" in hist else None

    current = _num(closes.iloc[-1])
    previous = _num(closes.iloc[-2]) if len(closes) >= 2 else None
    volume = int(volumes.iloc[-1]) if volumes is not None and _num(volumes.iloc[-1]) is not None else None

    avg_volume = None
    if volumes is not None and len(volumes) >= 2:
        prior = volumes.iloc[:-1].tail(AVG_VOLUME_SESSIONS)
        avg_volume = _num(prior.mean(), 0)
    volume_ratio = round(volume / avg_volume, 2) if volume is not None and avg_volume else None

    currency = None
    try:
        currency = ticker.fast_info.get("currency")
    except Exception:
        pass

    next_earn = _next_earnings(ticker)
    return {
        "symbol": symbol,
        "currency": currency,
        "current_price": current,
        "previous_close": previous,
        "daily_movement_pct": _pct(current, previous),
        "volume": volume,
        "avg_volume": int(avg_volume) if avg_volume is not None else None,
        "volume_ratio": volume_ratio,
        "next_earnings_date": next_earn.isoformat() if next_earn else None,
        "days_to_earnings": _days_until(next_earn),
        "as_of_date": _to_date(hist.index[-1]).isoformat() if _to_date(hist.index[-1]) else None,
    }


def _history(symbol: str, period: str) -> Dict[str, Any]:
    ticker = yf.Ticker(symbol)
    hist = _load_history(ticker, period)

    closes = hist["Close"]
    bars = []
    prev_close = None
    for idx, row in hist.iterrows():
        close = _num(row.get("Close"))
        vol = row.get("Volume")
        bars.append({
            "date": _to_date(idx).isoformat() if _to_date(idx) else str(idx),
            "close": close,
            "volume": int(vol) if _num(vol) is not None else None,
            "change_pct": _pct(close, prev_close),
        })
        prev_close = close

    truncated = len(bars) > MAX_HISTORY_BARS
    high_col = hist["High"] if "High" in hist else closes
    low_col = hist["Low"] if "Low" in hist else closes
    return {
        "symbol": symbol,
        "period": period,
        "period_change_pct": _pct(_num(closes.iloc[-1]), _num(closes.iloc[0])),
        "period_high": _num(high_col.max()),
        "period_low": _num(low_col.min()),
        "bars_truncated": truncated,
        "bars": bars[-MAX_HISTORY_BARS:],
    }


def _earnings(symbol: str) -> Dict[str, Any]:
    ticker = yf.Ticker(symbol)
    next_earn = _next_earnings(ticker)

    recent: List[Dict[str, Any]] = []
    recent_error = None
    try:
        df = ticker.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            today = date.today()
            for idx, row in df.iterrows():
                d = _to_date(idx)
                reported = _num(row.get("Reported EPS"))
                if d is None or d > today or reported is None:
                    continue
                recent.append({
                    "date": d.isoformat(),
                    "eps_estimate": _num(row.get("EPS Estimate")),
                    "eps_reported": reported,
                    "surprise_pct": _num(row.get("Surprise(%)")),
                })
                if len(recent) >= 4:
                    break
            if next_earn is None:
                upcoming = sorted(d for d in (_to_date(i) for i in df.index) if d and d >= today)
                next_earn = upcoming[0] if upcoming else None
    except Exception as e:
        recent_error = f"{type(e).__name__}: {e}"

    result: Dict[str, Any] = {
        "symbol": symbol,
        "next_earnings_date": next_earn.isoformat() if next_earn else None,
        "days_to_earnings": _days_until(next_earn),
        "recent": recent,
    }
    if recent_error and next_earn is None:
        result["error"] = f"Earnings data unavailable ({recent_error})"
    elif recent_error:
        result["recent_error"] = recent_error
    return result
