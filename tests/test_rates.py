"""Exchange rates: the ECB feed is read, checked and saved; anything wrong leaves the fixed table in use."""

import json

import httpx
import pytest

from cvmaker import rates, salary, settings, web_fetch

ECB = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time='2026-11-23'>
    <Cube currency='USD' rate='1.0939'/><Cube currency='GBP' rate='0.83673'/><Cube currency='PLN' rate='4.4588'/>
    <Cube currency='RON' rate='5.3483'/><Cube currency='CHF' rate='0.9186'/><Cube currency='JPY' rate='174.01'/>
    <Cube currency='BGN' rate='3.0'/>
  </Cube></Cube>
</gesmes:Envelope>"""


@pytest.fixture
def feed(monkeypatch, tmp_path):
    """feed(body or status) makes the ECB answer that; rates start empty in a throw-away folder."""
    monkeypatch.setattr(settings, "LIVE_RATES", True)
    monkeypatch.setattr(rates, "_path", lambda: tmp_path / "rates.json")
    monkeypatch.setattr(rates, "_loaded", None)
    monkeypatch.setattr(rates, "_tried", 0.0)
    calls = []

    def install(answer):
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(answer) if isinstance(answer, int) else httpx.Response(200, content=answer)

        monkeypatch.setattr(web_fetch, "http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
        return calls

    return install


def test_parse_ecb():
    date, parsed = rates.parse_ecb(ECB)
    assert date == "2026-11-23"
    assert parsed["USD"] == pytest.approx(1 / 1.0939) and parsed["GBP"] == pytest.approx(1 / 0.83673)


def test_refresh_saves_and_is_used_for_salaries(feed, tmp_path):
    calls = feed(ECB)
    assert rates.current() == rates.FALLBACK and rates.as_of() == ""
    assert rates.refresh() is True
    now = rates.current()
    assert now["USD"] == pytest.approx(1 / 1.0939) and now["CHF"] == pytest.approx(1 / 0.9186)
    assert now["BGN"] == rates.FALLBACK["BGN"] and now["EUR"] == 1.0      # fixed: never taken from the feed
    assert "JPY" not in now                                                # only currencies the app converts
    assert rates.as_of() == "2026-11-23"
    assert salary.monthly_eur("$120,000") == round(120000 / 12 / 1.0939)
    assert json.loads((tmp_path / "rates.json").read_text())["date"] == "2026-11-23"
    # fresh for a day: not fetched again
    assert rates.refresh() is False and len(calls) == 1
    # another process (or the next start) reads them from the file
    rates._loaded = None
    assert rates.current()["USD"] == pytest.approx(1 / 1.0939)


@pytest.mark.parametrize("answer", [
    500,
    b"not xml at all",
    ECB.replace(b"1.0939", b"109.39").replace(b"0.83673", b"83.673").replace(b"4.4588", b"0.044")
       .replace(b"5.3483", b"0.05").replace(b"0.9186", b"91.86"),         # every rate far off: a mangled feed
])
def test_a_bad_answer_keeps_the_fixed_table(feed, tmp_path, answer):
    calls = feed(answer)
    assert rates.refresh() is False
    assert rates.current() == rates.FALLBACK
    assert not (tmp_path / "rates.json").exists()
    assert rates.refresh() is False and len(calls) == 1                    # not retried on every search


def test_one_implausible_rate_is_dropped_the_rest_kept(feed):
    feed(ECB.replace(b"rate='1.0939'", b"rate='0.0011'"))
    assert rates.refresh() is True
    assert rates.current()["USD"] == rates.FALLBACK["USD"]
    assert rates.current()["GBP"] == pytest.approx(1 / 0.83673)


def test_switched_off(feed, monkeypatch):
    calls = feed(ECB)
    monkeypatch.setattr(settings, "LIVE_RATES", False)
    assert rates.refresh(force=True) is False and calls == []
