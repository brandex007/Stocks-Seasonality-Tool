"""Tests for the data layer's source selection and Stooq parsing (no network)."""

import pandas as pd
import pytest

from seasonality import data as data_lib
from seasonality.data import DataError, parse_stooq_csv, stooq_symbol

STOOQ_CSV = """Date,Open,High,Low,Close,Volume
1968-04-01,38.00,38.20,37.90,38.10,0
1968-04-02,38.10,38.40,38.00,38.30,0
1968-04-03,38.30,38.60,38.20,38.55,0
"""


def test_commodity_symbols_all_map_to_stooq():
    """Every Yahoo futures ticker offered in the app needs a Stooq equivalent —
    Yahoo's continuous contracts only start around 2000."""
    from seasonality.assets import PRESETS

    for label, ticker in PRESETS["Commodities"].items():
        assert stooq_symbol(ticker), f"no Stooq symbol for {label} ({ticker})"


def test_stooq_symbol_guesses():
    assert stooq_symbol("GC=F") == "xauusd"
    assert stooq_symbol("CL=F") == "cl.f"
    assert stooq_symbol("^GSPC") == "^spx"
    assert stooq_symbol("AAPL") == "aapl.us"
    assert stooq_symbol("BTC-USD") == "btcusd"
    assert stooq_symbol("EURUSD=X") == "eurusd"
    assert stooq_symbol("ZZ=F") is None      # unmapped future: don't invent one
    assert stooq_symbol("") is None


def test_parse_stooq_csv():
    df = parse_stooq_csv(STOOQ_CSV, "xauusd")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("1968-04-01")
    assert df["close"].iloc[-1] == pytest.approx(38.55)


def test_parse_stooq_csv_rejects_error_pages():
    with pytest.raises(DataError):
        parse_stooq_csv("Exceeded the daily hits limit", "xauusd")
    with pytest.raises(DataError):
        parse_stooq_csv("<html><body>nope</body></html>", "xauusd")


def test_auto_prefers_the_earlier_series(monkeypatch, tmp_path):
    """Auto is the whole point for commodities: Yahoo gold starts in 2000."""
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)

    yahoo = pd.DataFrame(
        {"close": range(300)},
        index=pd.bdate_range("2000-08-30", periods=300),
    )
    stooq = pd.DataFrame(
        {"close": range(9000)},
        index=pd.bdate_range("1968-04-01", periods=9000),
    )
    monkeypatch.setattr(data_lib, "download", lambda t: yahoo)
    monkeypatch.setattr(data_lib, "download_stooq", lambda s, **kw: stooq)

    auto = data_lib.load_history("GC=F", source="auto")
    assert auto.attrs["source"] == "stooq"
    assert auto.attrs["symbol"] == "xauusd"
    assert auto.index[0].year == 1968

    forced = data_lib.load_history("GC=F", source="yahoo", force_refresh=True)
    assert forced.attrs["source"] == "yahoo"
    assert forced.index[0].year == 2000


def test_auto_keeps_yahoo_when_stooq_adds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    idx = pd.bdate_range("1990-01-01", periods=5000)
    monkeypatch.setattr(data_lib, "download", lambda t: pd.DataFrame({"close": range(5000)}, index=idx))
    monkeypatch.setattr(
        data_lib, "download_stooq",
        lambda s, **kw: pd.DataFrame({"close": range(4000)}, index=pd.bdate_range("1995-01-01", periods=4000)),
    )
    df = data_lib.load_history("^GSPC", source="auto")
    assert df.attrs["source"] == "yahoo"


def test_stooq_source_without_a_mapping_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    with pytest.raises(DataError):
        data_lib.load_history("ZZ=F", source="stooq")


def test_auto_falls_back_to_stooq_when_yahoo_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    stooq = pd.DataFrame({"close": range(600)}, index=pd.bdate_range("2010-01-01", periods=600))

    def boom(_t):
        raise DataError("yahoo is down")

    monkeypatch.setattr(data_lib, "download", boom)
    monkeypatch.setattr(data_lib, "download_stooq", lambda s, **kw: stooq)
    df = data_lib.load_history("GC=F", source="auto")
    assert df.attrs["source"] == "stooq"


def test_source_label():
    df = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2020-01-01"]))
    df.attrs.update(source="stooq", symbol="xauusd")
    assert data_lib.source_label(df) == "Stooq · xauusd"


def test_auto_does_not_probe_stooq_for_plain_equities(monkeypatch, tmp_path):
    """Guessing aapl.us for every ticker would double the requests for nothing."""
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    calls = []
    idx = pd.bdate_range("2000-01-01", periods=500)
    monkeypatch.setattr(data_lib, "download", lambda t: pd.DataFrame({"close": range(500)}, index=idx))
    stub = pd.DataFrame({"close": range(10)}, index=pd.bdate_range("1990-01-01", periods=10))
    monkeypatch.setattr(data_lib, "download_stooq", lambda s, **kw: (calls.append(s), stub)[1])

    df = data_lib.load_history("AAPL", source="auto")
    assert df.attrs["source"] == "yahoo"
    assert calls == []                        # curated map only

    data_lib.load_history("GC=F", source="auto", force_refresh=True)
    assert calls == ["xauusd"]                # ...but commodities are probed


def test_a_failing_stooq_probe_is_not_retried_every_rerun(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib._FAILED.clear()
    idx = pd.bdate_range("2000-01-01", periods=500)
    monkeypatch.setattr(data_lib, "download", lambda t: pd.DataFrame({"close": range(500)}, index=idx))

    attempts = []

    def timeout(symbol, **kw):
        attempts.append(symbol)
        raise DataError("timed out")

    monkeypatch.setattr(data_lib, "download_stooq", timeout)
    for _ in range(4):
        data_lib.load_history("GC=F", source="auto", force_refresh=True)
    assert attempts == ["xauusd"]             # one attempt, then a cooldown
    data_lib._FAILED.clear()
