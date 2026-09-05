"""Price data loading with a local on-disk cache.

Daily history is downloaded from Yahoo Finance (via yfinance) and cached as
parquet (or CSV if pyarrow is unavailable) under ``data/cache/``. Subsequent
loads read the cache until it is older than ``max_age_hours`` or a refresh is
forced.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


class DataError(RuntimeError):
    """Raised when price history cannot be obtained."""


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())


def _cache_file(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401

        return CACHE_DIR / f"{_safe_name(ticker)}.parquet"
    except ImportError:
        return CACHE_DIR / f"{_safe_name(ticker)}.csv"


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


def load_history(
    ticker: str,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> pd.DataFrame:
    """Return cached daily history for ``ticker``, refreshing when stale.

    Falls back to the cached copy if the network is unavailable.
    """
    ticker = ticker.strip()
    if not ticker:
        raise DataError("No ticker given.")

    path = _cache_file(ticker)
    cached = _read_cache(path)
    fresh_enough = (
        cached is not None
        and path.exists()
        and (time.time() - path.stat().st_mtime) < max_age_hours * 3600
    )
    if cached is not None and fresh_enough and not force_refresh:
        return cached

    try:
        df = download(ticker)
    except DataError:
        if cached is not None:
            return cached  # offline: serve whatever we already have
        raise
    _write_cache(df, path)
    return df


def cache_info(ticker: str) -> dict:
    path = _cache_file(ticker)
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
    targets = [_cache_file(ticker)] if ticker else list(CACHE_DIR.iterdir())
    n = 0
    for p in targets:
        if p.exists() and p.is_file():
            p.unlink()
            n += 1
    return n
