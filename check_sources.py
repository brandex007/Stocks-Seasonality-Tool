#!/usr/bin/env python3
"""Report which source and how much history each asset actually gets.

Stooq symbols are mapped by convention in ``seasonality/data.py`` and can only be
confirmed against the live endpoint, so run this once after setup to see what
resolves:

    python check_sources.py                 # every commodity preset
    python check_sources.py --all           # every preset in the app
    python check_sources.py GC=F SI=F ^SPX  # specific tickers

A ticker that falls back to Yahoo with a start date around 2000 means its Stooq
symbol needs fixing in ``STOOQ_MAP``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from seasonality import assets as asset_lib
from seasonality import data as data_lib

logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # keep the table readable


def check(ticker: str, refresh: bool) -> dict:
    row = {"ticker": ticker, "stooq_symbol": data_lib.stooq_symbol(ticker) or "—"}
    for source in ("yahoo", "stooq"):
        try:
            df = data_lib.load_history(ticker, source=source, force_refresh=refresh)
            row[source] = f"{df.index[0]:%Y-%m-%d}  ({len(df):,} days)"
        except data_lib.DataError as exc:
            row[source] = f"— {str(exc).split(':')[0][:38]}"
    return row


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tickers", nargs="*", help="tickers to check (default: commodities)")
    p.add_argument("--all", action="store_true", help="check every preset asset")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    a = p.parse_args(argv)

    if a.tickers:
        tickers = a.tickers
    elif a.all:
        tickers = list(asset_lib.all_presets().values())
    else:
        tickers = list(asset_lib.PRESETS["Commodities"].values())

    print(f"{'ticker':<10} {'stooq':<9} {'yahoo starts':<26} {'stooq starts':<26}")
    print("-" * 74)
    wins = 0
    for t in tickers:
        r = check(t, a.refresh)
        print(f"{r['ticker']:<10} {r['stooq_symbol']:<9} {r['yahoo']:<26} {r['stooq']:<26}")
        if not r["stooq"].startswith("—"):
            wins += 1
    print("-" * 74)
    print(f"{wins}/{len(tickers)} resolved on Stooq")
    if wins < len(tickers):
        print("Unresolved symbols fall back to Yahoo — fix them in seasonality/data.py STOOQ_MAP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
