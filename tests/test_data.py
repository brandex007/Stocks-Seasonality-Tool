"""Tests for the data layer: source selection, FRED parsing, splicing (no network)."""

import numpy as np
import pandas as pd
import pytest

from seasonality import data as data_lib
from seasonality.data import DataError, fred_series, parse_fred_csv, splice

FRED_CSV = """observation_date,GOLDAMGBD228NLBM
1968-04-01,38.00
1968-04-02,.
1968-04-03,38.30
1968-04-04,38.55
"""

FRED_CSV_LEGACY_HEADER = """DATE,DCOILWTICO
1986-01-02,25.56
1986-01-03,26.00
"""


def _series(start, periods, value, end=None):
    idx = pd.bdate_range(end=end, periods=periods) if end else pd.bdate_range(start, periods=periods)
    return pd.DataFrame({"close": np.full(periods, float(value))}, index=idx)


def test_map_covers_the_benchmarks_with_long_history():
    for ticker in ("CL=F", "BZ=F", "NG=F"):
        assert fred_series(ticker), f"no FRED series mapped for {ticker}"
    # ids are looked up, never guessed — a wrong guess is just a wasted request
    assert fred_series("ZC=F") is None
    assert fred_series("AAPL") is None
    assert fred_series("") is None
    assert fred_series(" cl=f ") == "DCOILWTICO"           # tolerant of user input


def test_dead_precious_metal_ids_are_not_mapped():
    """FRED removed the LBMA gold and silver fixings; both ids 404 now, so
    mapping them just costs a failed request on every load."""
    assert fred_series("GC=F") is None
    assert fred_series("SI=F") is None
    assert "GOLDAMGBD228NLBM" not in data_lib.FRED_MAP.values()


def test_parse_fred_csv_handles_missing_values_and_both_headers():
    df = parse_fred_csv(FRED_CSV, "GOLDAMGBD228NLBM")
    assert list(df.columns) == ["close"]
    assert len(df) == 3                                     # the "." row is dropped
    assert df.index[0] == pd.Timestamp("1968-04-01")
    assert df["close"].iloc[-1] == pytest.approx(38.55)

    legacy = parse_fred_csv(FRED_CSV_LEGACY_HEADER, "DCOILWTICO")
    assert len(legacy) == 2 and legacy["close"].iloc[0] == pytest.approx(25.56)


def test_parse_fred_csv_rejects_non_csv_bodies():
    """Stooq's bot-challenge HTML is exactly what this guard is for."""
    with pytest.raises(DataError):
        parse_fred_csv("<!DOCTYPE html><html><body>verify your browser</body></html>", "X")
    with pytest.raises(DataError):
        parse_fred_csv("", "X")


def test_splice_scales_the_old_series_to_meet_the_new_one():
    """The London fix and the COMEX contract sit at different levels; only the
    level is adjusted, so each year's shape is untouched."""
    old = _series("2015-01-01", 2000, 100.0)       # discontinued benchmark
    new = _series("2020-01-01", 1000, 150.0)       # current series, 1.5x the level
    combined, join = splice(old, new)

    assert join is not None
    assert combined.index[0] == old.index[0]
    assert combined.index[-1] == new.index[-1]
    assert combined.loc[combined.index < join, "close"].iloc[0] == pytest.approx(150.0)
    assert combined.loc[join, "close"] == pytest.approx(150.0)
    assert not combined.index.duplicated().any()
    assert combined.index.is_monotonic_increasing


def test_splice_preserves_relative_moves():
    """A 10% move in the old series is still a 10% move after scaling — which is
    all seasonality reads."""
    idx = pd.bdate_range("2010-01-01", periods=1500)
    old = pd.DataFrame({"close": np.linspace(100, 200, len(idx))}, index=idx)
    new = pd.DataFrame(
        {"close": np.linspace(400, 500, 800)},
        index=pd.bdate_range(idx[-800], periods=800),
    )
    combined, join = splice(old, new)
    head = combined.loc[combined.index < join, "close"]
    old_head = old.loc[old.index < join, "close"]
    assert np.allclose(
        head.pct_change().dropna().values, old_head.pct_change().dropna().values
    )


def test_splice_refuses_without_a_usable_overlap():
    old = _series("1990-01-01", 500, 100.0)
    new = _series("2020-01-01", 500, 150.0)
    combined, join = splice(old, new)
    assert join is None
    assert combined.equals(new)                    # no join beats a fabricated one


def test_auto_splices_a_discontinued_benchmark(monkeypatch, tmp_path):
    """A discontinued benchmark still has to reach today's price."""
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    fred = _series("1968-04-01", 14000, 100.0)
    yahoo = _series("2000-08-30", 6500, 150.0)
    monkeypatch.setattr(data_lib, "download", lambda t: yahoo)
    monkeypatch.setattr(data_lib, "download_fred", lambda s, **kw: fred)

    df = data_lib.load_history("CL=F", source="auto")
    assert df.attrs["source"] == "fred+yahoo"
    assert df.attrs["spliced_at"] is not None
    assert df.index[0].year == 1968
    assert df.index[-1] == yahoo.index[-1]
    assert "joined" in data_lib.source_label(df)


def test_auto_uses_fred_alone_when_it_is_still_current(monkeypatch, tmp_path):
    """WTI: DCOILWTICO runs to today, so there is nothing to splice."""
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    fred = _series("1986-01-02", 10000, 50.0)
    yahoo = _series(None, 6000, 50.0, end=fred.index[-1])
    monkeypatch.setattr(data_lib, "download", lambda t: yahoo)
    monkeypatch.setattr(data_lib, "download_fred", lambda s, **kw: fred)

    df = data_lib.load_history("CL=F", source="auto")
    assert df.attrs["source"] == "fred"
    assert df.index[0].year == 1986


def test_auto_keeps_yahoo_when_fred_adds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    monkeypatch.setattr(data_lib, "download", lambda t: _series("1990-01-01", 8000, 10.0))
    monkeypatch.setattr(data_lib, "download_fred", lambda s, **kw: _series("1999-01-01", 6000, 10.0))
    df = data_lib.load_history("EURUSD=X", source="auto")
    assert df.attrs["source"] == "yahoo"


def test_auto_does_not_probe_fred_for_unmapped_tickers(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    calls = []
    monkeypatch.setattr(data_lib, "download", lambda t: _series("2000-01-01", 500, 10.0))
    monkeypatch.setattr(
        data_lib, "download_fred",
        lambda s, **kw: (calls.append(s), _series("1990-01-01", 500, 10.0))[1],
    )
    data_lib.load_history("AAPL", source="auto")
    assert calls == []
    data_lib.load_history("CL=F", source="auto", force_refresh=True)
    assert calls == ["DCOILWTICO"]


def test_fallback_reason_is_reported(monkeypatch, tmp_path):
    """When FRED fails, say so instead of silently serving the short series."""
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    monkeypatch.setattr(data_lib, "download", lambda t: _series("2000-08-30", 500, 10.0))

    def refuse(series, **kw):
        raise DataError(f"FRED request failed for '{series}': HTTP 503")

    monkeypatch.setattr(data_lib, "download_fred", refuse)
    df = data_lib.load_history("CL=F", source="auto")
    assert df.attrs["source"] == "yahoo"
    assert "503" in df.attrs["fallback_reason"]

    again = data_lib.load_history("CL=F", source="auto", force_refresh=True)
    assert "failed recently" in again.attrs["fallback_reason"]
    data_lib.reset_failures()


def test_a_failing_probe_is_not_retried_every_rerun(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    data_lib.reset_failures()
    monkeypatch.setattr(data_lib, "download", lambda t: _series("2000-01-01", 500, 10.0))
    attempts = []

    def timeout(series, **kw):
        attempts.append(series)
        raise DataError("timed out")

    monkeypatch.setattr(data_lib, "download_fred", timeout)
    for _ in range(4):
        data_lib.load_history("CL=F", source="auto", force_refresh=True)
    assert attempts == ["DCOILWTICO"]
    data_lib.reset_failures()


def test_explicit_fred_source_without_a_mapping_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(data_lib, "CACHE_DIR", tmp_path)
    with pytest.raises(DataError):
        data_lib.load_history("GC=F", source="fred")


def test_source_label():
    df = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2020-01-01"]))
    df.attrs.update(source="fred", symbol="DCOILWTICO")
    assert data_lib.source_label(df) == "FRED · DCOILWTICO"
