"""Exchange rates to EUR for comparing salaries: the European Central Bank's daily reference rates, kept in
`rates.json` in the data folder, with a fixed table for when they have never been fetched (offline, or switched
off with CVMAKER_LIVE_RATES=0).

Nothing here fetches on its own: `refresh()` is called when a search starts (which is on the network anyway), at
most once a day. Reading the rates never waits for the network."""

import json
import threading
import time
import xml.etree.ElementTree as ET

from . import log, settings

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MAX_AGE = 24 * 3600

# EUR per 1 unit. BGN is the fixed conversion rate (Bulgaria adopted the euro on 1 Jan 2026; older postings still
# quote лв.), so it is never fetched. The others are rough values, replaced by the ECB's when those are known.
FALLBACK = {"EUR": 1.0, "BGN": 1 / 1.95583, "USD": 0.92, "GBP": 1.17, "PLN": 0.23, "RON": 0.20, "CHF": 1.05}
FIXED = {"EUR", "BGN"}

_lock = threading.Lock()
_loaded: dict | None = None      # {"fetched": unix time, "date": "2026-11-23", "rates": {...}} as read from the file
_tried = 0.0                     # when a fetch was last attempted (failed ones too, so a dead network isn't retried per search)


def _path():
    return settings.DATA_ROOT / "rates.json"


def _load() -> dict:
    global _loaded
    if _loaded is None:
        try:
            data = json.loads(_path().read_text())
            _loaded = {"fetched": float(data["fetched"]), "date": str(data.get("date", "")), "rates": _usable(data["rates"])}
        except (OSError, ValueError, KeyError, TypeError):
            _loaded = {"fetched": 0.0, "date": "", "rates": {}}
    return _loaded


def _usable(rates: dict) -> dict[str, float]:
    """Only the currencies the app converts, and only values near the fixed table: a wrong or mangled feed must not
    turn a salary into nonsense."""
    out = {}
    for code, fallback in FALLBACK.items():
        if code in FIXED:
            continue
        try:
            value = float(rates[code])
        except (KeyError, TypeError, ValueError):
            continue
        if fallback / 2 <= value <= fallback * 2:
            out[code] = value
    return out


def current() -> dict[str, float]:
    """EUR per unit of each currency the app knows: fetched rates where there are any, the fixed table otherwise."""
    with _lock:
        return {**FALLBACK, **_load()["rates"]}


def as_of() -> str:
    """The ECB date of the rates in use, or '' when the fixed table is used."""
    with _lock:
        return _load()["date"] if _load()["rates"] else ""


def parse_ecb(xml: bytes) -> tuple[str, dict[str, float]]:
    """(date, {currency: EUR per unit}) from the ECB's daily XML, which quotes units per EUR."""
    date, rates = "", {}
    for cube in ET.fromstring(xml).iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
        date = cube.get("time") or date
        code, rate = cube.get("currency"), cube.get("rate")
        if code and rate:
            try:
                rates[code] = 1 / float(rate)
            except (ValueError, ZeroDivisionError):
                continue
    return date, rates


def refresh(force: bool = False) -> bool:
    """Fetch today's rates if the saved ones are older than a day. Returns whether new rates were saved; never raises
    (the search that calls it must not fail over exchange rates)."""
    global _loaded, _tried
    if not settings.LIVE_RATES:
        return False
    with _lock:
        now = time.time()
        if not force and (now - _load()["fetched"] < MAX_AGE or now - _tried < 3600):
            return False
        _tried = now
    try:
        from .web_fetch import http

        with http() as c:
            r = c.get(ECB_URL, timeout=10)
            r.raise_for_status()
        date, rates = parse_ecb(r.content)
        rates = _usable(rates)
        if not rates:
            raise ValueError("no usable rates in the ECB feed")
    except Exception as e:  # network, XML, anything: keep what we have
        log.warning("exchange rates not updated (%s: %s)", type(e).__name__, e)
        return False
    data = {"fetched": now, "date": date, "rates": rates}
    with _lock:
        _loaded = data
        try:
            tmp = _path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(_path())
        except OSError as e:
            log.warning("exchange rates not saved (%s)", e)
    return True
