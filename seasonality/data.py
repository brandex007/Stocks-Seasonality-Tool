"""Price data loading with a local on-disk cache.

Two sources:

* **Yahoo Finance** (via yfinance) — everything, but its continuous futures
  series are short. ``GC=F`` starts in 2000, ``CL=F`` in 2000, and so on, which
  leaves only five or six midterm years to average.
* **FRED** (St. Louis Fed) — a documented free CSV API with the long daily
  benchmark series: LBMA gold and silver from 1968, WTI from 1986, Brent from
  1987, Henry Hub gas from 1997.

``load_history(..., source="auto")`` fetches both where a FRED equivalent is
known and keeps whichever starts earlier, so commodities get their full history
without the caller having to think about it. Both are cached per source under
``data/cache/``.

Some FRED benchmarks were discontinued (the LBMA gold and silver fixings ended in
2023), so a long FRED history is spliced onto the current Yahoo series: the two
are ratio-matched over their overlap, which keeps the shape of every year intact
while ending on today's price. A FRED series is also not always the same
instrument as its Yahoo counterpart — the London fix rather than the front COMEX
contract — so the source, symbol and any splice are reported in ``df.attrs`` for
the UI to display.

Stooq used to fill this role. It now gates its CSV endpoint behind a JavaScript
proof-of-work bot challenge, so it is no longer usable from a script.
"""

from __future__ import annotations

import io
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
_UA = "Mozilla/5.0 (compatible; seasonality-explorer/1.0)"

#: Yahoo symbol -> FRED series id, for daily series that reach further back.
#: Commodities are the main reason this exists: every Yahoo ``=F`` continuous
#: contract starts around 2000. Series marked "ended" are spliced onto Yahoo.
#: Gold and silver are absent on purpose: FRED carried the LBMA fixings
#: (GOLDAMGBD228NLBM, SLVPRUSD) until they were discontinued *and removed* - both
#: ids now 404 - and its remaining precious-metal series are monthly, which
#: cannot drive a daily path. See ``check_sources.py --probe``.
FRED_MAP: dict[str, str] = {
    "CL=F": "DCOILWTICO",         # WTI spot,         1986-
    "BZ=F": "DCOILBRENTEU",       # Brent spot,       1987-
    "NG=F": "DHHNGSP",            # Henry Hub gas,    1997-
    "^VIX": "VIXCLS",             # VIX,              1990-
    "^TNX": "DGS10",              # US 10y yield,     1962-
    "EURUSD=X": "DEXUSEU",        # 1999-
    "USDJPY=X": "DEXJPUS",        # 1971-
    "GBPUSD=X": "DEXUSUK",        # 1971-
}

MIN_SPLICE_OVERLAP = 20  # trading days needed to ratio-match two series

SOURCES = ("auto", "yahoo", "fred")


class DataError(RuntimeError):
    """Raised when price history cannot be obtained."""


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())


def _cache_file(ticker: str, source: str = "yahoo") -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(ticker) + ("" if source == "yahoo" else f"__{source}")
    try:
        import pyarrow  # noqa: F401

        return CACHE_DIR / f"{stem}.parquet"
    except ImportError:
        return CACHE_DIR / f"{stem}.csv"


def fred_series(ticker: str) -> str | None:
    """FRED series id for a Yahoo ticker, or None if there isn't one.

    Deliberately a lookup rather than a guess: FRED ids bear no relation to
    exchange tickers, and inventing one would just cost a failed request.
    """
    return FRED_MAP.get(ticker.strip().upper()) or None


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
    except Exception:
        return None
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index)
    return df


def _write_cache(df: pd.DataFrame, path: Path) -> None:
    try:
        if path.suffix == ".parquet":
            df.to_parquet(path)
        else:
            df.to_csv(path)
    except Exception:
        pass  # a failed cache write must never break the app


def _flatten(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalise yfinance output to lowercase single-level OHLCV columns."""
    if isinstance(raw.columns, pd.MultiIndex):
        levels = [list(raw.columns.get_level_values(i)) for i in range(raw.columns.nlevels)]
        # keep whichever level actually holds the price field names
        price_names = {"open", "high", "low", "close", "adj close", "volume"}
        chosen = 0
        for i, vals in enumerate(levels):
            if {str(v).lower() for v in vals} & price_names:
                chosen = i
                break
        raw = raw.copy()
        raw.columns = raw.columns.get_level_values(chosen)
    raw = raw.rename(columns=lambda c: str(c).strip().lower())
    raw = raw.loc[:, ~raw.columns.duplicated()]
    if "close" not in raw.columns:
        if "adj close" in raw.columns:
            raw["close"] = raw["adj close"]
        else:
            raise DataError(f"No close price returned for '{ticker}'.")
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in raw.columns]
    out = raw[keep].copy()
    out.index = pd.to_datetime(out.index)
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])


def download(ticker: str) -> pd.DataFrame:
    """Download the full daily history for ``ticker`` from Yahoo Finance."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise DataError("yfinance is not installed. Run: pip install -r requirements.txt") from exc

    try:
        raw = yf.download(
            ticker,
            period="max",
            interval="1d",
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
        )
    except Exception as exc:
        raise DataError(f"Download failed for '{ticker}': {exc}") from exc

    if raw is None or len(raw) == 0:
        raise DataError(f"No data returned for '{ticker}'. Check the symbol.")
    return _flatten(raw, ticker)


def parse_fred_csv(text: str, series: str) -> pd.DataFrame:
    """Parse a FRED CSV into a close-only frame.

    FRED writes missing observations as ``.`` and has used both ``DATE`` and
    ``observation_date`` for the first column over the years.
    """
    stripped = text.lstrip()
    if not stripped[:1].isalpha() or "," not in stripped[:200]:
        snippet = " ".join(text.split())[:140] or "empty response"
        raise DataError(f"FRED returned no data for '{series}': {snippet}")

    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip().lower() for c in df.columns]
    date_col = next((c for c in ("observation_date", "date") if c in df.columns), None)
    value_col = next((c for c in df.columns if c != date_col), None)
    if date_col is None or value_col is None:
        raise DataError(f"Unexpected FRED columns for '{series}': {list(df.columns)}")

    out = pd.DataFrame(
        # .to_numpy(): passing the Series itself would align on its RangeIndex
        # against the DatetimeIndex and leave every value NaN
        {"close": pd.to_numeric(df[value_col], errors="coerce").to_numpy()},  # "." -> NaN
        index=pd.to_datetime(df[date_col], errors="coerce"),
    )
    out = out[out.index.notna()].dropna().sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if out.empty:
        raise DataError(f"FRED returned an empty series for '{series}'.")
    return out


def download_fred(series: str, timeout: float = 20.0) -> pd.DataFrame:
    """Download a full daily history from FRED."""
    url = FRED_URL.format(series=series)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DataError(f"FRED request failed for '{series}': {exc}") from exc
    return parse_fred_csv(text, series)


def splice(old: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Chain a discontinued long history onto the current series.

    The LBMA gold and silver fixings ended in 2023, so their FRED history has to
    be joined to Yahoo's to reach today. The old series is scaled by the median
    price ratio over the overlap — a level shift only, so every year's *shape*,
    which is all seasonality cares about, is untouched. Returns the combined
    close-only frame and the join date (None when no splice happened).
    """
    overlap = old.index.intersection(new.index)
    if len(overlap) < MIN_SPLICE_OVERLAP:
        return new, None

    tail = overlap[-min(len(overlap), 60):]
    ratios = (new.loc[tail, "close"] / old.loc[tail, "close"]).replace(
        [float("inf"), float("-inf")], pd.NA
    ).dropna()
    if ratios.empty:
        return new, None
    ratio = float(ratios.median())
    if not (ratio > 0) or not pd.notna(ratio):
        return new, None

    join = overlap[-1]
    head = old.loc[old.index < join, ["close"]] * ratio
    combined = pd.concat([head, new.loc[new.index >= join, ["close"]]])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined, join


def _cached_or_download(ticker: str, source: str, fetch, force_refresh: bool,
                        max_age_hours: float):
    """Shared cache-then-download path. Returns (df, error)."""
    path = _cache_file(ticker, source)
    cached = _read_cache(path)
    fresh = (
        cached is not None
        and path.exists()
        and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    )
    if cached is not None and fresh and not force_refresh:
        return cached, None
    try:
        df = fetch()
    except DataError as exc:
        return cached, exc  # cached may be None; caller decides
    _write_cache(df, path)
    return df, None


_FAILED: dict[str, float] = {}
_FAILURE_COOLDOWN_S = 600.0


def _recently_failed(symbol: str) -> bool:
    """Don't re-probe a Stooq symbol that just timed out on every rerun."""
    ts = _FAILED.get(symbol)
    return ts is not None and (time.time() - ts) < _FAILURE_COOLDOWN_S


def _note_failure(symbol: str) -> None:
    _FAILED[symbol] = time.time()


def reset_failures() -> None:
    """Forget the Stooq cooldown — used by the app's refresh button."""
    _FAILED.clear()


def _tag(df: pd.DataFrame, source: str, symbol: str) -> pd.DataFrame:
    df = df.copy()
    df.attrs["source"] = source
    df.attrs["symbol"] = symbol
    return df


def load_history(
    ticker: str,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
    source: str = "auto",
) -> pd.DataFrame:
    """Return cached daily history for ``ticker``, refreshing when stale.

    ``source`` is ``"yahoo"``, ``"fred"`` or ``"auto"``. Auto asks Yahoo first
    and, when a FRED equivalent is known, keeps whichever series starts earlier —
    splicing the two when the FRED benchmark has since been discontinued. That is
    how commodities get history before 2000. The source, symbol and any splice
    date are recorded in ``df.attrs``. Falls back to the cached copy when the
    network is unavailable.
    """
    ticker = ticker.strip()
    if not ticker:
        raise DataError("No ticker given.")
    if source not in SOURCES:
        raise DataError(f"Unknown source '{source}'. Use one of {SOURCES}.")

    series = fred_series(ticker)

    if source == "fred":
        if not series:
            raise DataError(f"No FRED series is known for '{ticker}'.")
        df, err = _cached_or_download(
            ticker, "fred", lambda: download_fred(series), force_refresh, max_age_hours
        )
        if df is None:
            raise err or DataError(f"No FRED data for '{ticker}'.")
        return _tag(df, "fred", series)

    y_df, y_err = _cached_or_download(
        ticker, "yahoo", lambda: download(ticker), force_refresh, max_age_hours
    )
    if source == "yahoo":
        if y_df is None:
            raise y_err or DataError(f"No data for '{ticker}'.")
        return _tag(y_df, "yahoo", ticker)

    # Auto only asks FRED for the mapped symbols — the commodities, rates and FX
    # where Yahoo is short. Everything else would just cost a failed request.
    f_df = None
    note = ""
    if series:
        if _recently_failed(series):
            note = f"FRED probe for '{series}' failed recently; retrying later."
        else:
            f_df, f_err = _cached_or_download(
                ticker, "fred",
                lambda: download_fred(series, timeout=8.0),
                force_refresh, max_age_hours,
            )
            if f_df is None and f_err is not None:
                _note_failure(series)
                note = str(f_err)

    # an empty frame is no data, whatever produced it
    y_df = None if (y_df is not None and y_df.empty) else y_df
    f_df = None if (f_df is not None and f_df.empty) else f_df

    if y_df is None and f_df is None:
        raise y_err or DataError(f"No data for '{ticker}'.")
    if y_df is None:
        return _tag(f_df, "fred", series)
    if f_df is None:
        out = _tag(y_df, "yahoo", ticker)
        if note:
            # say why the longer series isn't on screen instead of silently
            # serving the short one
            out.attrs["fallback_reason"] = note
        return out

    earlier_by = (y_df.index[0] - f_df.index[0]).days
    if earlier_by <= 365 or len(f_df) < 250:
        return _tag(y_df, "yahoo", ticker)

    # FRED starts earlier. If it also runs to today, use it as-is; if the
    # benchmark was discontinued, splice Yahoo onto the end.
    stale_days = (y_df.index[-1] - f_df.index[-1]).days
    if stale_days <= 7:
        return _tag(f_df, "fred", series)

    combined, join = splice(f_df, y_df)
    if join is None:
        # no usable overlap: the longer series alone beats a bad join
        out = _tag(f_df, "fred", series)
        out.attrs["fallback_reason"] = (
            f"'{series}' ends {f_df.index[-1]:%Y-%m-%d} and does not overlap "
            f"{ticker}; showing the FRED series alone."
        )
        return out
    out = _tag(combined, "fred+yahoo", f"{series} → {ticker}")
    out.attrs["spliced_at"] = join
    return out


def source_label(df: pd.DataFrame) -> str:
    """Human-readable 'FRED · DCOILWTICO' style label for a loaded history."""
    src = df.attrs.get("source", "yahoo")
    sym = df.attrs.get("symbol", "")
    name = {
        "yahoo": "Yahoo Finance",
        "fred": "FRED",
        "fred+yahoo": "FRED spliced to Yahoo",
    }.get(src, src)
    label = f"{name} · {sym}" if sym else name
    join = df.attrs.get("spliced_at")
    if join is not None:
        label += f" (joined {join:%Y-%m-%d})"
    return label


def cache_info(ticker: str, source: str = "yahoo") -> dict:
    path = _cache_file(ticker, source)
    if not path.exists():
        return {"cached": False}
    return {
        "cached": True,
        "path": str(path),
        "age_hours": (time.time() - path.stat().st_mtime) / 3600,
    }


def clear_cache(ticker: str | None = None) -> int:
    """Delete cache files. Returns the number removed."""
    if not CACHE_DIR.exists():
        return 0
    if ticker:
        targets = [_cache_file(ticker, s) for s in ("yahoo", "fred")]
    else:
        targets = list(CACHE_DIR.iterdir())
    n = 0
    for p in targets:
        if p.exists() and p.is_file():
            p.unlink()
            n += 1
    return n
