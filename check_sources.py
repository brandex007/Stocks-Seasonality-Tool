#!/usr/bin/env python3
"""Report which source and how much history each asset actually gets.

FRED series ids are mapped in ``seasonality/data.py`` and can only be confirmed
against the live endpoint, so run this once after setup to see what resolves:

    python check_sources.py                 # every commodity preset
    python check_sources.py --all           # every preset in the app
    python check_sources.py GC=F SI=F ^SPX  # specific tickers

A ticker that falls back to Yahoo with a start date around 2000 means its FRED
series id needs fixing in ``FRED_MAP``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from seasonality import assets as asset_lib
from seasonality import data as data_lib

logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # keep the table readable


def check(ticker: str, refresh: bool) -> dict:
    row = {"ticker": ticker, "stooq_symbol": data_lib.fred_series(ticker) or "—"}
    for source in ("yahoo", "fred"):
        try:
            df = data_lib.load_history(ticker, source=source, force_refresh=refresh)
            row[source] = f"{df.index[0]:%Y-%m-%d}  ({len(df):,} days)"
            row[f"{source}_error"] = ""
        except data_lib.DataError as exc:
            row[source] = "— failed"
            row[f"{source}_error"] = str(exc)  # kept whole: the tail is the diagnosis
    return row


def raw_probe(symbol: str, timeout: float = 20.0) -> str:
    """Show exactly what FRED sends back, headers and all."""
    import urllib.error
    import urllib.request

    url = data_lib.FRED_URL.format(series=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": data_lib._UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(400).decode("utf-8", errors="replace")
            return (f"HTTP {resp.status} · {resp.headers.get('content-type', '?')} · "
                    f"{url}\n{body.strip()[:400]}")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} · {url}\n{exc.read(300).decode('utf-8', errors='replace')}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc} · {url}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*", help="tickers to check (default: commodities)")
    p.add_argument("--all", action="store_true", help="check every preset asset")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    p.add_argument("--raw", action="store_true",
                   help="print FRED's raw response for each symbol (what's really wrong)")
    a = p.parse_args(argv)

    if a.tickers:
        tickers = a.tickers
    elif a.all:
        tickers = list(asset_lib.all_presets().values())
    else:
        tickers = list(asset_lib.PRESETS["Commodities"].values())

    print(f"{'ticker':<10} {'fred':<12} {'yahoo starts':<26} {'fred starts':<26}")
    print("-" * 74)
    wins = 0
    rows = []
    for t in tickers:
        r = check(t, a.refresh)
        rows.append(r)
        print(f"{r['ticker']:<10} {r['stooq_symbol']:<12} {r['yahoo']:<26} {r['fred']:<26}")
        if not r["fred"].startswith("—"):
            wins += 1
    print("-" * 74)
    print(f"{wins}/{len(tickers)} resolved on FRED")

    failures = [r for r in rows if r["fred"].startswith("—")]
    if failures:
        print("\nFRED errors in full:")
        for r in failures:
            print(f"  {r['ticker']:<8} {r['fred_error']}")
        print("\nRe-run with --raw to see the exact response body.")

    if a.raw:
        print("\nRaw responses:")
        for r in rows:
            if r["stooq_symbol"] != "—":
                print(f"\n--- {r['ticker']} ({r['stooq_symbol']}) ---")
                print(raw_probe(r["stooq_symbol"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
