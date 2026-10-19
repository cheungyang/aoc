"""Money amounts and their daily exchange rates to USD.

`parse_money` turns "€123.45", "123.45 EUR", "NT$1,200", "-$6.40" or 9.17 into an
exact Decimal and, when the text names one, an ISO 4217 code. `usd_rate` gives the
USD rate of a currency on a date (a receipt's) or today, from two free, keyless
sources:

- ECB reference rates through Frankfurter, for the ~30 currencies the ECB
  publishes. A weekend or holiday gets the previous business day's rate, but
  not one from more than MAX_DAYS_BACK days before the day asked for.
- fawazahmed0/currency-api (jsDelivr, with its Cloudflare mirror) for every other
  currency (TWD is not an ECB currency), and as the fallback when Frankfurter
  can't answer, or only with an older rate.

Rates are cached per (date, currency) for the life of the process: a past date's
rate never changes, and "latest" is keyed by today's date. When no source answers,
FxError says why. Callers must not fall back to an estimate.
"""
import re
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import partial
from typing import Callable, Dict, List, Optional, Tuple, Union

import requests

CENT = Decimal("0.01")

# Currency markers as printed, matched exactly. A bare "$" reads as USD and a bare
# "¥" as JPY, the usual case for award taxes; anything else ambiguous ("kr", "R")
# has to come with its ISO code.
SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "JP¥": "JPY",
    "CN¥": "CNY", "RMB": "CNY", "C$": "CAD", "CA$": "CAD", "A$": "AUD", "AU$": "AUD",
    "NZ$": "NZD", "HK$": "HKD", "S$": "SGD", "SG$": "SGD", "NT$": "TWD", "MX$": "MXN",
    "R$": "BRL", "₩": "KRW", "₹": "INR", "₺": "TRY", "₱": "PHP", "฿": "THB", "₪": "ILS",
    "₫": "VND", "zł": "PLN", "Kč": "CZK", "Ft": "HUF",
}

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/{day}"
CURRENCY_API_URLS = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{code}.json",
    "https://{day}.currency-api.pages.dev/v1/currencies/{code}.json",
)
TIMEOUT_S = 10
# A day with no rate of its own takes the closest earlier one, at most this many
# days back: currency-api's last daily file, or the ECB's last business day.
MAX_DAYS_BACK = 3

# The ECB's reference-rate currencies (BGN left with Bulgaria's euro adoption in 2026).
ECB_CURRENCIES = frozenset(
    "AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR "
    "NOK NZD PHP PLN RON SEK SGD THB TRY ZAR".split()
)

_NUMBER = re.compile(r"\d[\d,.' ]*")
_MINUS = ("-", "\u2212")

Amount = Union[int, float, Decimal, str]


class MoneyError(ValueError):
    """An amount that can't be read exactly."""


class FxError(Exception):
    """No source could give the rate. The message says what each source answered."""


# --------------------------------------------------------------------------- amounts

def to_decimal(value: Union[int, float, Decimal]) -> Decimal:
    """An exact Decimal for a number (9.17 stays 9.17, not its binary neighbour)."""
    if isinstance(value, bool):
        raise MoneyError(f"{value!r} is not an amount")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        raise MoneyError(f"{value!r} is not an amount") from None
    if not number.is_finite():
        raise MoneyError(f"{value!r} is not an amount")
    return number


def parse_money(value: Amount, default_currency: Optional[str] = None) -> Tuple[Decimal, Optional[str]]:
    """(amount, ISO code) for a number or a printed amount like "€123.45" or "123.45 EUR".

    A bare number takes `default_currency`. Raises MoneyError for text with no amount,
    an unknown or ambiguous currency marker, or a number that could be read two ways.
    """
    if isinstance(value, (bool, int, float, Decimal)):
        return to_decimal(value), default_currency
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    match = _NUMBER.search(text)
    if not match:
        raise MoneyError(f"no amount in {value!r}")
    prefix, suffix = text[:match.start()], text[match.end():]
    # The minus may sit on either side of a symbol: "-$6.40", "$-6.40".
    for minus in _MINUS:
        if minus in prefix:
            negative, prefix = True, prefix.replace(minus, "")
    prefix, suffix = prefix.strip(), suffix.strip()
    if prefix and suffix:
        raise MoneyError(f"can't tell the currency of {value!r}")
    currency = _currency(prefix or suffix, value) or default_currency
    amount = _number(match.group().strip(), value)
    return (-amount if negative else amount), currency


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_usd(amount: Decimal) -> str:
    """"$1,234.56", "-$6.40"."""
    rounded = cents(amount)
    return f"{'-' if rounded < 0 else ''}${abs(rounded):,.2f}"


def _currency(marker: str, original: Amount) -> Optional[str]:
    if not marker:
        return None
    if marker in SYMBOLS:
        return SYMBOLS[marker]
    if re.fullmatch(r"[A-Za-z]{3}", marker):
        return marker.upper()
    raise MoneyError(f"unknown currency {marker!r} in {original!r}; give its ISO code, e.g. '450 SEK'")


def _number(text: str, original: Amount) -> Decimal:
    """Reads "1,234.56", "1.234,56", "1'234.50" or "123,45"; refuses what could be read two ways."""
    digits = text.replace(" ", "").replace("'", "")
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", digits):
            digits = digits.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", digits):
            digits = digits.replace(",", ".")
        else:
            raise MoneyError(f"can't read the number in {original!r}")
    elif digits.count(".") > 1:
        if not re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):
            raise MoneyError(f"can't read the number in {original!r}")
        digits = digits.replace(".", "")
    try:
        return Decimal(digits)
    except InvalidOperation:
        raise MoneyError(f"can't read the number in {original!r}") from None


# --------------------------------------------------------------------------- rates

@dataclass(frozen=True)
class Rate:
    currency: str
    usd: Decimal  # USD per one unit of `currency`
    date: str  # the date the rate was published for, YYYY-MM-DD
    source: str  # "ECB", "currency-api", or "par" for USD itself

    def describe(self) -> str:
        """"1 TWD = 0.031274 USD (currency-api, 2026-08-13)"."""
        return f"1 {self.currency} = {self.usd:.6g} USD ({self.source}, {self.date})"


def to_usd(amount: Decimal, rate: Rate) -> Decimal:
    """`amount` of `rate.currency` in USD, to the cent."""
    return cents(amount * rate.usd)


_cache: Dict[Tuple[str, str], Rate] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def usd_rate(currency: str, on: Optional[date] = None, today: Optional[date] = None) -> Rate:
    """The USD rate of `currency` on `on` (None: today's latest).

    `today` is the caller's local date. A date up to a day ahead of it is a receipt
    from a timezone that's already tomorrow, and gets the latest rate; anything
    later is refused. A past day with no rate of its own gets the closest earlier
    one, at most MAX_DAYS_BACK days back.
    """
    code = (currency or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise FxError(f"{currency!r} is not an ISO currency code like 'EUR' or 'TWD'")
    if today is None:
        from core.util.time_util import get_local_now
        today = get_local_now().date()
    if on is not None and on > today + timedelta(days=1):
        raise FxError(f"{on.isoformat()} is in the future; no exchange rate exists for it yet")
    if code == "USD":
        return Rate("USD", Decimal(1), (on or today).isoformat(), "par")

    day = None if on is None or on >= today else on
    key = (day.isoformat() if day else f"latest:{today.isoformat()}", code)
    with _lock:
        if key in _cache:
            return _cache[key]

    errors: List[str] = []
    for source in _sources(code, today):
        try:
            rate = source(code, day)
        except FxError as e:
            errors.append(str(e))
            continue
        with _lock:
            _cache[key] = rate
        return rate
    when = f"on {day.isoformat()}" if day else "today"
    raise FxError(f"No USD rate for {code} {when} or the {MAX_DAYS_BACK} days before: " + "; ".join(errors))


def _sources(code: str, today: date) -> List[Callable[[str, Optional[date]], Rate]]:
    ecb = partial(_frankfurter, today=today)
    return [ecb, _currency_api] if code in ECB_CURRENCIES else [_currency_api]


def _frankfurter(code: str, day: Optional[date], today: date) -> Rate:
    """Asks for the ECB's own EUR quotes and crosses them, so JPY keeps its precision.

    For a weekend or holiday the ECB answers with its last business day's rate. One
    from more than MAX_DAYS_BACK days before the day asked for (`day`, or `today`
    for the latest) doesn't count, and neither does one with no date.
    """
    symbols = "USD" if code == "EUR" else f"USD,{code}"
    url = FRANKFURTER_URL.format(day=day.isoformat() if day else "latest")
    data = _get_json(url, "ECB", params={"symbols": symbols})
    rates = (data or {}).get("rates") or {}
    if "USD" not in rates or (code != "EUR" and code not in rates):
        raise FxError(f"ECB (Frankfurter) has no {code} rate")
    try:
        published = date.fromisoformat(str(data.get("date")))
    except ValueError:
        raise FxError(f"ECB (Frankfurter) sent no date with its {code} rate") from None
    asked = day or today
    if (asked - published).days > MAX_DAYS_BACK:
        raise FxError(f"ECB's {code} rate is from {published.isoformat()}, "
                      f"more than {MAX_DAYS_BACK} days before {asked.isoformat()}")
    usd_per_eur = Decimal(str(rates["USD"]))
    usd = usd_per_eur if code == "EUR" else usd_per_eur / Decimal(str(rates[code]))
    return Rate(code, usd, published.isoformat(), "ECB")


def _currency_api(code: str, day: Optional[date]) -> Rate:
    days = ["latest"] if day is None else [(day - timedelta(days=n)).isoformat() for n in range(MAX_DAYS_BACK + 1)]
    for tag in days:
        data = _currency_api_file(tag, code.lower())
        if data is None:
            continue  # no file for that day: the day before is the rate in force
        usd = (data.get(code.lower()) or {}).get("usd")
        if usd is None:
            raise FxError(f"currency-api has no {code} rate")
        return Rate(code, Decimal(str(usd)), str(data.get("date") or tag), "currency-api")
    raise FxError(f"currency-api has no {code} file from {days[-1]} to {days[0]}")


def _currency_api_file(tag: str, code: str) -> Optional[dict]:
    """The day's file from the first mirror that answers; None when it doesn't exist."""
    errors = []
    for template in CURRENCY_API_URLS:
        try:
            return _get_json(template.format(day=tag, code=code), "currency-api")
        except FxError as e:
            errors.append(str(e))
    raise FxError("; ".join(errors))


def _get_json(url: str, source: str, params: Optional[dict] = None) -> Optional[dict]:
    """The JSON object at `url`; None on 404; FxError on anything else."""
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise FxError(f"{source} unreachable ({type(e).__name__})") from None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise FxError(f"{source} answered HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise FxError(f"{source} sent invalid JSON") from None
    if not isinstance(data, dict):
        raise FxError(f"{source} sent an unexpected answer")
    return data
