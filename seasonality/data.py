"""Price data loading with a local on-disk cache.

Two sources:

* **Yahoo Finance** (via yfinance) — everything, but its continuous futures
  series are short. ``GC=F`` starts in 2000, ``CL=F`` in 2000, and so on, which
  leaves only five or six midterm years to average.
* **Stooq** — free daily CSVs whose commodity and index histories reach much
  further back (spot gold as ``xauusd``, for instance).

``load_history(..., source="auto")`` fetches both where a Stooq equivalent is
known and keeps whichever starts earlier, so commodities get their full history
without the caller having to think about it. Both are cached per source under
``data/cache/``.

Note that a Stooq series is not always the same instrument as its Yahoo
counterpart — spot gold rather than the front futures contract, say. For
seasonality that is fine, but the source and symbol actually used are reported
in ``df.attrs`` so the UI can say which one is on screen.
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

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
_STOOQ_UA = "Mozilla/5.0 (compatible; seasonality-explorer/1.0)"

#: Yahoo symbol -> Stooq symbol, for series where Stooq reaches further back.
#: Commodities are the main reason this exists: every Yahoo ``=F`` continuous
#: contract starts around 2000.
STOOQ_MAP: dict[str, str] = {
    # metals
    "GC=F": "xauusd",   # spot gold
    "SI=F": "xagusd",   # spot silver
    "PL=F": "xptusd",
    "PA=F": "xpdusd",
    "HG=F": "hg.f",
    "ALI=F": "ali.f",
    # energy
    "CL=F": "cl.f",     # WTI
    "BZ=F": "cb.f",     # Brent
    "NG=F": "ng.f",
    "RB=F": "rb.f",
    "HO=F": "ho.f",
    # grains & softs
    "ZC=F": "zc.f",     # corn
    "ZW=F": "zw.f",     # wheat
    "ZS=F": "zs.f",     # soybeans
    "ZL=F": "zl.f",
    "ZM=F": "zm.f",
    "KC=F": "kc.f",     # coffee
    "SB=F": "sb.f",     # sugar
    "CC=F": "cc.f",     # cocoa
    "CT=F": "ct.f",     # cotton
    "LE=F": "le.f",     # live cattle
    "HE=F": "he.f",     # lean hogs
    # indices
    "^GSPC": "^spx",
    "^DJI": "^dji",
    "^IXIC": "^ndq",
    "^NDX": "^ndx",
    "^RUT": "^rut",
    "^VIX": "^vix",
    "^N225": "^nkx",
    "^GDAXI": "^dax",
    "^FTSE": "^ukx",
    # fx
    "EURUSD=X": "eurusd",
    "USDJPY=X": "usdjpy",
    "GBPUSD=X": "gbpusd",
}

SOURCES = ("auto", "yahoo", "stooq")


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


def stooq_symbol(ticker: str) -> str | None:
    """Best-guess Stooq symbol for a Yahoo ticker, or None if there isn't one."""
    t = ticker.strip()
    if not t:
        return None
    mapped = STOOQ_MAP.get(t.upper())
    if mapped:
        return mapped
    if t.startswith("^"):
        return t.lower()
    if t.upper().endswith("-USD"):                       # BTC-USD -> btcusd
        return t.lower().replace("-", "")
    if t.upper().endswith("=X"):                         # EURUSD=X -> eurusd
        return t[:-2].lower()
    if t.endswith("=F"):
        return None                                      # unmapped future: no guess
    if re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", t):     # US equity / ETF
        return f"{t.lower()}.us"
    return None


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


def parse_stooq_csv(text: str, symbol: str) -> pd.DataFrame:
    """Parse Stooq's daily CSV into the same shape as :func:`_flatten`."""
    head = text.lstrip()[:200].lower()
    if not head.startswith("date"):
        # Stooq answers with plain text on a bad symbol or a throttled request
        snippet = " ".join(text.split())[:120] or "empty response"
        raise DataError(f"Stooq returned no data for '{symbol}': {snippet}")

    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" not in df.columns or "close" not in df.columns:
        raise DataError(f"Unexpected Stooq columns for '{symbol}': {list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).set_index("date").sort_index()
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep]
    df = df[~df.index.duplicated(keep="last")]
    if df.empty:
        raise DataError(f"Stooq returned an empty series for '{symbol}'.")
    return df


def download_stooq(symbol: str, timeout: float = 20.0) -> pd.DataFrame:
    """Download a full daily history from Stooq."""
    url = STOOQ_URL.format(symbol=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": _STOOQ_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DataError(f"Stooq request failed for '{symbol}': {exc}") from exc
    return parse_stooq_csv(text, symbol)


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

    ``source`` is ``"yahoo"``, ``"stooq"`` or ``"auto"``. Auto asks Yahoo first
    and, when a Stooq equivalent is known, keeps whichever series starts earlier
    — which is how commodities get history before 2000. The source and symbol
    actually used are recorded in ``df.attrs``. Falls back to the cached copy
    when the network is unavailable.
    """
    ticker = ticker.strip()
    if not ticker:
        raise DataError("No ticker given.")
    if source not in SOURCES:
        raise DataError(f"Unknown source '{source}'. Use one of {SOURCES}.")

    alt = stooq_symbol(ticker)

    if source == "stooq":
        if not alt:
            raise DataError(f"No Stooq symbol is known for '{ticker}'.")
        df, err = _cached_or_download(
            ticker, "stooq", lambda: download_stooq(alt), force_refresh, max_age_hours
        )
        if df is None:
            raise err or DataError(f"No Stooq data for '{ticker}'.")
        return _tag(df, "stooq", alt)

    y_df, y_err = _cached_or_download(
        ticker, "yahoo", lambda: download(ticker), force_refresh, max_age_hours
    )
    if source == "yahoo":
        if y_df is None:
            raise y_err or DataError(f"No data for '{ticker}'.")
        return _tag(y_df, "yahoo", ticker)

    # Auto only probes Stooq for curated symbols — the commodities and indices
    # where Yahoo is short. Guessing "aapl.us" for every equity would double the
    # requests to gain nothing.
    s_df = None
    s_note = ""
    if alt and ticker.upper() in STOOQ_MAP:
        if _recently_failed(alt):
            s_note = f"Stooq probe for '{alt}' failed recently; retrying later."
        else:
            s_df, s_err = _cached_or_download(
                ticker, "stooq",
                lambda: download_stooq(alt, timeout=8.0),
                force_refresh, max_age_hours,
            )
            if s_df is None and s_err is not None:
                _note_failure(alt)
                s_note = str(s_err)
    # an empty frame is no data, whatever produced it
    y_df = None if (y_df is not None and y_df.empty) else y_df
    s_df = None if (s_df is not None and s_df.empty) else s_df

    if y_df is None and s_df is None:
        raise y_err or DataError(f"No data for '{ticker}'.")
    if y_df is None:
        return _tag(s_df, "stooq", alt)
    if s_df is None:
        out = _tag(y_df, "yahoo", ticker)
        if s_note:
            # say why the longer series isn't on screen instead of silently
            # serving the short one
            out.attrs["fallback_reason"] = s_note
        return out

    earlier_by = (y_df.index[0] - s_df.index[0]).days
    if earlier_by > 365 and len(s_df) > 250:
        return _tag(s_df, "stooq", alt)
    return _tag(y_df, "yahoo", ticker)


def source_label(df: pd.DataFrame) -> str:
    """Human-readable 'Stooq · xauusd' style label for a loaded history."""
    src = df.attrs.get("source", "yahoo")
    sym = df.attrs.get("symbol", "")
    name = {"yahoo": "Yahoo Finance", "stooq": "Stooq"}.get(src, src)
    return f"{name} · {sym}" if sym else name


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
        targets = [_cache_file(ticker, s) for s in ("yahoo", "stooq")]
    else:
        targets = list(CACHE_DIR.iterdir())
    n = 0
    for p in targets:
        if p.exists() and p.is_file():
            p.unlink()
            n += 1
    return n
