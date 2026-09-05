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
CUSTOM_DIR = Path(__file__).resolve().parent.parent / "data" / "custom"

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
_UA = "Mozilla/5.0 (compatible; seasonality-explorer/1.0)"

#: Yahoo symbol -> FRED series id, for daily series that reach further back.
#: Commodities are the main reason this exists: every Yahoo ``=F`` continuous
#: contract starts around 2000. Series marked "ended" are spliced onto Yahoo.
#: Gold and silver are absent on purpose: FRED carried the LBMA fixings
#: (GOLDAMGBD228NLBM, SLVPRUSD) until they were discontinued *and removed* — both
#: ids now 404 — and its remaining precious-metal series are monthly, which
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

#: Daily series fetched from a public dataset repo at runtime, as
#: ``ticker -> (url, price column)``. Referenced rather than vendored: the repo
#: holds a URL, not somebody else's price data. FRED carried the LBMA gold and
#: silver fixings until it removed them, and nothing else free covers 1968-.
REMOTE_CSV: dict[str, tuple[str, str]] = {
    "GC=F": (
        "https://raw.githubusercontent.com/unbalancedparentheses/forex-centuries"
        "/main/data/sources/lbma/lbma_gold_daily.csv",
        "gold_pm_usd",
    ),
    "SI=F": (
        "https://raw.githubusercontent.com/unbalancedparentheses/forex-centuries"
        "/main/data/sources/lbma/lbma_silver_daily.csv",
        "silver_usd",
    ),
}

MIN_SPLICE_OVERLAP = 20  # trading days needed to ratio-match two series

SOURCES = ("auto", "yahoo", "fred", "github", "custom")


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


#: Column names accepted in a user CSV, lowercased. First match wins.
_DATE_COLUMNS = ("date", "observation_date", "time", "day", "datetime", "timestamp")
_PRICE_COLUMNS = ("close", "adj close", "adjusted close", "price", "value", "last",
                  "usd", "usd (am)", "usd (pm)", "settle", "close/last")


def custom_path(ticker: str) -> Path | None:
    """Find a user-supplied CSV for ``ticker`` under ``data/custom/``.

    Both spellings work — ``GC=F.csv`` and the filesystem-safe ``GC_F.csv`` —
    and the match is case-insensitive, because that is what people actually type.
    """
    if not CUSTOM_DIR.exists():
        return None
    wanted = {_safe_name(ticker).lower(), ticker.strip().lower()}
    for path in sorted(CUSTOM_DIR.iterdir()):
        if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
            continue
        if path.stem.lower() in wanted or _safe_name(path.stem).lower() in wanted:
            return path
    return None


def read_custom_csv(path: Path) -> pd.DataFrame:
    """Parse a user CSV into a close-only frame.

    Deliberately forgiving about what a downloaded price file looks like: any of
    the usual date and price column names, either sort order, thousands
    separators, currency symbols, and blank rows.
    """
    sep = "\t" if path.suffix.lower() == ".tsv" else None
    try:
        raw = pd.read_csv(path, sep=sep, engine="python")
    except Exception as exc:
        raise DataError(f"Could not read '{path.name}': {exc}") from exc
    if raw.empty:
        raise DataError(f"'{path.name}' has no rows.")

    raw.columns = [str(c).strip().lower() for c in raw.columns]
    date_col = next((c for c in _DATE_COLUMNS if c in raw.columns), None)
    price_col = next((c for c in _PRICE_COLUMNS if c in raw.columns), None)
    if date_col is None:                       # fall back to the first column
        date_col = raw.columns[0]
    if price_col is None:
        others = [c for c in raw.columns if c != date_col]
        if not others:
            raise DataError(f"'{path.name}' needs a date column and a price column.")
        price_col = others[-1] if len(others) == 1 else others[0]

    values = (
        raw[price_col].astype(str)
        .str.replace(r"[,$£€\s]", "", regex=True)
        .replace({"": None, ".": None, "-": None, "n/a": None, "na": None})
    )
    out = pd.DataFrame(
        {"close": pd.to_numeric(values, errors="coerce").to_numpy()},
        index=pd.to_datetime(raw[date_col], errors="coerce", format="mixed"),
    )
    out = out[out.index.notna()].dropna().sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if out.empty:
        raise DataError(
            f"'{path.name}': found no usable rows using columns "
            f"'{date_col}' and '{price_col}'."
        )
    return out


def load_custom(ticker: str) -> pd.DataFrame | None:
    """Read the user CSV for ``ticker`` if there is one."""
    path = custom_path(ticker)
    return read_custom_csv(path) if path else None


def custom_tickers() -> list[str]:
    """Every ticker with a user CSV waiting in ``data/custom/``."""
    if not CUSTOM_DIR.exists():
        return []
    return sorted(
        p.stem for p in CUSTOM_DIR.iterdir()
        if p.suffix.lower() in (".csv", ".tsv", ".txt")
    )


def drop_weekend_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Remove Saturday/Sunday rows from a fixing series.

    The London fixes are weekday auctions, so a weekend row is a data artefact —
    and in practice a bad one: the silver file carries 7.54 for Saturday
    1983-02-05 against ~14.10 either side. Returns the cleaned frame and the
    dates dropped, so callers can say what they removed.
    """
    weekend = df.index.dayofweek >= 5
    return df[~weekend], [f"{d:%Y-%m-%d}" for d in df.index[weekend]]


def parse_remote_csv(text: str, price_col: str, label: str) -> pd.DataFrame:
    """Parse a fetched dataset CSV, keeping one named price column."""
    head = text.lstrip()[:200].lower()
    if "," not in head or head.startswith("<"):
        snippet = " ".join(text.split())[:140] or "empty response"
        raise DataError(f"{label} did not return CSV: {snippet}")

    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip().lower() for c in df.columns]
    date_col = next((c for c in _DATE_COLUMNS if c in df.columns), df.columns[0])
    if price_col not in df.columns:
        raise DataError(
            f"{label}: no '{price_col}' column (found {list(df.columns)}). "
            "The upstream file's layout may have changed."
        )
    out = pd.DataFrame(
        {"close": pd.to_numeric(df[price_col], errors="coerce").to_numpy()},
        index=pd.to_datetime(df[date_col], errors="coerce"),
    )
    out = out[out.index.notna()].dropna().sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out[out["close"] > 0]
    out, _ = drop_weekend_rows(out)
    if out.empty:
        raise DataError(f"{label} returned no usable rows.")
    return out


def download_remote(ticker: str, timeout: float = 20.0) -> pd.DataFrame:
    """Fetch the mapped dataset CSV for ``ticker``."""
    entry = REMOTE_CSV.get(ticker.strip().upper())
    if not entry:
        raise DataError(f"No dataset URL is known for '{ticker}'.")
    url, price_col = entry
    label = url.rsplit("/", 1)[-1]
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DataError(f"Dataset request failed for '{label}': {exc}") from exc
    return parse_remote_csv(text, price_col, label)


def remote_label(ticker: str) -> str | None:
    entry = REMOTE_CSV.get(ticker.strip().upper())
    return entry[0].rsplit("/", 1)[-1] if entry else None


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


def splice(
    old: pd.DataFrame,
    new: pd.DataFrame,
    at: str = "start",
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Join a long history onto the current series, level-matched.

    ``at="start"`` (the default) hands over at the **first** date the two share,
    so the long series only fills the years before the current one begins and
    everything from there on is the live, split/dividend-adjusted data. That is
    what "aggregate the two" normally means: use the good recent series, and
    borrow history for the gap in front of it.

    ``at="end"`` hands over at the last shared date instead, keeping one
    instrument for as long as possible - right for a benchmark that was
    discontinued and has only a stub of the new series after it.

    Either way the old series is scaled by the median price ratio over the 60
    shared days nearest the join, so the seam has no step in it. That is a level
    shift only: every year's shape, which is all seasonality reads, is untouched.
    Returns the combined close-only frame and the join date (None if the two
    don't overlap enough to level-match, in which case ``new`` is returned as-is).
    """
    overlap = old.index.intersection(new.index)
    if len(overlap) < MIN_SPLICE_OVERLAP:
        return new, None

    join = overlap[0] if at == "start" else overlap[-1]
    window = overlap[:60] if at == "start" else overlap[-60:]
    ratios = (new.loc[window, "close"] / old.loc[window, "close"]).replace(
        [float("inf"), float("-inf")], pd.NA
    ).dropna()
    if ratios.empty:
        return new, None
    ratio = float(ratios.median())
    if not (ratio > 0) or not pd.notna(ratio):
        return new, None

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

    ``source`` is ``"yahoo"``, ``"fred"``, ``"custom"`` or ``"auto"``. Auto takes
    Yahoo as the current series and looks for a longer one to put in front of it:
    a CSV you dropped in ``data/custom/`` first, then a mapped dataset URL, then
    FRED. When that longer
    series stops before today — a discontinued benchmark, or a one-off download
    of pre-2000 history — the two are spliced. The source, symbol and any splice
    date are recorded in ``df.attrs``. Falls back to the cached copy when the
    network is unavailable.
    """
    ticker = ticker.strip()
    if not ticker:
        raise DataError("No ticker given.")
    if source not in SOURCES:
        raise DataError(f"Unknown source '{source}'. Use one of {SOURCES}.")

    series = fred_series(ticker)

    if source == "custom":
        df = load_custom(ticker)
        if df is None:
            raise DataError(
                f"No CSV for '{ticker}' in {CUSTOM_DIR}. Name it "
                f"'{_safe_name(ticker)}.csv' with a date column and a price column."
            )
        return _tag(df, "custom", custom_path(ticker).name)

    if source == "github":
        df, err = _cached_or_download(
            ticker, "github", lambda: download_remote(ticker), force_refresh, max_age_hours
        )
        if df is None:
            raise err or DataError(f"No dataset history for '{ticker}'.")
        return _tag(df, "github", remote_label(ticker) or ticker)

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

    # --- auto: find the longest history available for this ticker
    note = ""
    long_df = long_name = long_kind = None

    # A CSV you put there yourself is an explicit instruction, so it outranks
    # FRED and costs no request.
    try:
        long_df = load_custom(ticker)
        if long_df is not None:
            long_kind, long_name = "custom", custom_path(ticker).name
    except DataError as exc:
        note = str(exc)          # a malformed CSV should say so, not vanish

    if long_df is None and ticker.upper() in REMOTE_CSV:
        rlabel = remote_label(ticker)
        if _recently_failed(rlabel):
            note = note or f"Dataset fetch for '{rlabel}' failed recently; retrying later."
        else:
            r_df, r_err = _cached_or_download(
                ticker, "github",
                lambda: download_remote(ticker, timeout=10.0),
                force_refresh, max_age_hours,
            )
            if r_df is None and r_err is not None:
                _note_failure(rlabel)
                note = note or str(r_err)
            elif r_df is not None:
                long_df, long_kind, long_name = r_df, "github", rlabel

    if long_df is None and series:
        if _recently_failed(series):
            note = note or f"FRED probe for '{series}' failed recently; retrying later."
        else:
            f_df, f_err = _cached_or_download(
                ticker, "fred",
                lambda: download_fred(series, timeout=8.0),
                force_refresh, max_age_hours,
            )
            if f_df is None and f_err is not None:
                _note_failure(series)
                note = note or str(f_err)
            elif f_df is not None:
                long_df, long_kind, long_name = f_df, "fred", series

    # an empty frame is no data, whatever produced it
    y_df = None if (y_df is not None and y_df.empty) else y_df
    long_df = None if (long_df is not None and long_df.empty) else long_df

    if y_df is None and long_df is None:
        raise y_err or DataError(f"No data for '{ticker}'.")
    if y_df is None:
        return _tag(long_df, long_kind, long_name)
    if long_df is None:
        out = _tag(y_df, "yahoo", ticker)
        if note:
            # say why the longer series isn't on screen instead of silently
            # serving the short one
            out.attrs["fallback_reason"] = note
        return out

    earlier_by = (y_df.index[0] - long_df.index[0]).days
    if earlier_by <= 365 or len(long_df) < 250:
        out = _tag(y_df, "yahoo", ticker)
        if long_kind == "custom":
            out.attrs["fallback_reason"] = (
                f"'{long_name}' starts {long_df.index[0]:%Y-%m-%d}, no earlier than "
                f"Yahoo's {y_df.index[0]:%Y-%m-%d}, so it adds nothing."
            )
        return out

    # The long series wins the early years. Yahoo takes over from the day it
    # starts, so the recent decades are the adjusted, still-updating series and
    # the long source only fills the gap in front of it.
    combined, join = splice(long_df, y_df, at="start")
    if join is None:
        out = _tag(long_df, long_kind, long_name)
        out.attrs["fallback_reason"] = (
            f"'{long_name}' ends {long_df.index[-1]:%Y-%m-%d} and does not overlap "
            f"{ticker}; showing it alone, so the chart stops there."
        )
        return out
    out = _tag(combined, f"{long_kind}+yahoo", f"{long_name} → {ticker}")
    out.attrs["spliced_at"] = join
    return out


def source_label(df: pd.DataFrame) -> str:
    """Human-readable 'FRED · DCOILWTICO' style label for a loaded history."""
    src = df.attrs.get("source", "yahoo")
    sym = df.attrs.get("symbol", "")
    name = {
        "yahoo": "Yahoo Finance",
        "fred": "FRED",
        "custom": "Your CSV",
        "github": "LBMA dataset",
        "fred+yahoo": "FRED spliced to Yahoo",
        "custom+yahoo": "Your CSV spliced to Yahoo",
        "github+yahoo": "LBMA dataset spliced to Yahoo",
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
