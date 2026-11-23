"""Salary text: format structured salary fields, or find a salary mentioned in a posting's text."""

import re

from . import rates

CURRENCY_SIGNS = {"EUR": "€", "USD": "$", "GBP": "£"}
PERIODS = {
    "year": "/year", "yearly": "/year", "annual": "/year", "annually": "/year", "yr": "/year",
    "month": "/month", "monthly": "/month", "mo": "/month",
    "hour": "/hour", "hourly": "/hour", "hr": "/hour",
    "week": "/week", "weekly": "/week", "day": "/day", "daily": "/day",
}


def _amount(value) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{n / 1000:g}k" if n >= 10000 else f"{n:,.0f}"


def format_salary(low=None, high=None, currency: str | None = "", period: str | None = "") -> str:
    """'€60k-75k/year' from min/max/currency/period fields; '' when there is no amount."""
    if not low and not high:
        return ""
    code = (currency or "").upper()
    sign = CURRENCY_SIGNS.get(code, "")
    amounts = "-".join(_amount(v) for v in (low, high) if v)
    if low and high and low == high:
        amounts = _amount(low)
    text = f"{sign}{amounts}" if sign else f"{amounts} {code}".strip()
    return text + PERIODS.get((period or "").lower().strip(), "")


_CUR = r"(?:€|\$|£|EUR|USD|GBP|BGN|PLN|RON|CHF|лв\.?|лева|евро)"
_NUM = r"(?:\d{1,3}(?:[ ,. ]\d{3})+|\d+)(?:[.,]\d+)?\s?[kK]?"
_RANGE = rf"{_NUM}(?:\s?(?:-|–|—|to|до)\s?{_CUR}?\s?{_NUM})?"
_PATTERN = re.compile(
    rf"(?:{_CUR}\s?{_RANGE}|{_RANGE}\s?{_CUR})"
    r"(?:\s?(?:gross|net|brutto|netto|бруто|нето))?"
    r"(?:\s?(?:per|a|/|на)\s?(?:year|annum|month|hour|yr|mo|hr|jahr|monat|година|месец))?",
    re.I,
)
_CONTEXT = re.compile(
    r"salary|compensation|pay range|base pay|\bpay\b|remuneration|gross|net |per (?:year|annum|month|hour)"
    r"|/year|/month|/hour|/hr|\bOTE\b|brutto|netto|gehalt|/jahr|заплат|възнагражд|бруто|нето",
    re.I,
)
_NOT_SALARY = re.compile(r"^\s?(?:m\b|mn\b|million|bn\b|billion|funding|revenue|raised|in (?:funding|revenue))", re.I)


def _plausible(match: str) -> bool:
    first = re.search(r"\d[\d ,.\u00a0]*\s?[kK]?", match).group(0).strip()
    whole = re.sub(r"[ ,.\u00a0](?=\d{3}\b)", "", first)
    value = int(re.match(r"\d+", whole).group(0)) * (1000 if whole.lower().endswith("k") else 1)
    hourly = re.search(r"/\s?h|hour|hr\b", match, re.I)
    return value >= (10 if hourly else 300)


def find_salary(text: str) -> str:
    """The first salary-looking amount that appears near salary words, or ''."""
    if not text:
        return ""
    # Postings converted from HTML sometimes break a number across lines: "€87\n,200".
    text = re.sub(r"(\d)\s*\n\s*([,.]\d{3})", r"\1\2", text)
    for m in _PATTERN.finditer(text):
        found = " ".join(m.group(0).split())
        if not re.search(r"\d", found) or _NOT_SALARY.match(text[m.end(): m.end() + 20]):
            continue
        window = text[max(0, m.start() - 120): m.end() + 60]
        is_range = re.search(r"\d\s?[kK]?\s?(?:-|–|—|to|до)\s?\D{0,4}\d", found)
        if (_CONTEXT.search(window) or is_range) and _plausible(found):
            return found[:60]
    return ""


# Conversion to EUR, only for comparing salaries with each other: rates.py holds the ECB's daily rates when they
# have been fetched, and this fixed table otherwise.
EUR_RATES = rates.FALLBACK
_CURRENCY_WORDS = [
    ("BGN", r"лв|лева|\bbgn\b"), ("EUR", r"€|\beur\b|евро"), ("GBP", r"£|\bgbp\b"), ("USD", r"\$|\busd\b"),
    ("PLN", r"\bpln\b|zł"), ("RON", r"\bron\b|\blei\b"), ("CHF", r"\bchf\b"),
]
_HOURS_PER_MONTH, _DAYS_PER_MONTH, _WEEKS_PER_MONTH = 168, 21, 4.33


def _numbers(text: str) -> list[float]:
    """The amounts in a salary text: '€62,000 to €87,200' -> [62000, 87200]; '$80k' -> [80000]."""
    text = re.sub(r"(?<=\d)[  ,.](?=\d{3}\b)", "", text)
    out = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s?([kK])?", text):
        n = float(m.group(1).replace(",", "."))
        out.append(n * 1000 if m.group(2) else n)
    return out


def monthly_eur(text: str) -> float | None:
    """Approximate monthly amount in EUR of a salary text, for sorting and medians. None if unreadable.

    The period is taken from the text; without one, amounts of 15k or more are read as yearly and smaller
    ones as monthly (how Bulgarian and German postings usually quote). Net and gross are not converted."""
    if not text:
        return None
    low = text.lower()
    currency = next((code for code, pattern in _CURRENCY_WORDS if re.search(pattern, low)), None)
    amounts = [n for n in _numbers(low) if n > 0]
    if not currency or not amounts:
        return None
    if "task" in low or "project" in low:
        return None
    value = sum(amounts[:2]) / len(amounts[:2])
    if re.search(r"hour|/\s?h\b|\bhr\b|час", low):
        value *= _HOURS_PER_MONTH
    elif re.search(r"\bday\b|daily|/\s?day|ден", low):
        value *= _DAYS_PER_MONTH
    elif re.search(r"week", low):
        value *= _WEEKS_PER_MONTH
    elif re.search(r"month|/\s?mo\b|monat|месец", low):
        pass
    elif re.search(r"year|annum|annual|/\s?yr|jahr|годин", low) or value >= 15000:
        value /= 12
    value *= rates.current()[currency]
    return round(value) if 200 <= value <= 100000 else None


def format_monthly(value: float | None) -> str:
    if not value:
        return ""
    return f"≈ €{value / 1000:.1f}k/mo" if value >= 1000 else f"≈ €{value:.0f}/mo"
