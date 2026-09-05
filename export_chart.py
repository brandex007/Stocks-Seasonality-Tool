#!/usr/bin/env python3
"""Command-line chart export — same engine as the app, no UI.

Examples
--------
    python export_chart.py ^GSPC --window H2 --phase "Midterm year" -o sp500_h2.html
    python export_chart.py CL=F --start-month 3 --months 6 --years 1990:2025 -o crude.png
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from seasonality import assets as asset_lib
from seasonality import chart as chart_lib
from seasonality import data as data_lib
from seasonality.engine import CYCLE_PHASES, WINDOW_PRESETS, compute_seasonality

WINDOW_ALIASES = {
    "year": (1, 12), "full": (1, 12),
    "h1": (1, 6), "h2": (7, 6),
    "q1": (1, 3), "q2": (4, 3), "q3": (7, 3), "q4": (10, 3),
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Export a seasonality chart.")
    p.add_argument("ticker", help="Yahoo Finance symbol, e.g. ^GSPC, SPY, BTC-USD, GC=F")
    p.add_argument("--window", type=str.lower, choices=sorted(WINDOW_ALIASES),
                   help="preset window (year, h1, h2, q1-q4; case-insensitive)")
    p.add_argument("--start-month", type=int, default=1, help="1-12 (ignored with --window)")
    p.add_argument("--months", type=int, default=12, choices=[3, 6, 12])
    p.add_argument("--years", help="year range as START:END, e.g. 1950:2025")
    p.add_argument("--phase", action="append", choices=list(CYCLE_PHASES),
                   help="election-cycle subset to overlay (repeatable)")
    p.add_argument("--band", default="25,75", help="percentile band, or 'none'")
    p.add_argument("--median", action="store_true", help="use median instead of mean")
    p.add_argument("--no-current", action="store_true", help="hide the live-year path")
    p.add_argument("--only-filtered", action="store_true",
                   help="hide the all-years line and chart only the --phase years")
    p.add_argument("--no-day-ticks", action="store_true",
                   help="month names only on the x-axis (no day-of-month ticks)")
    p.add_argument("--refresh", action="store_true", help="force a fresh download")
    p.add_argument("-o", "--out", default="seasonality.html", help=".html or .png")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    start_month, months = (WINDOW_ALIASES[a.window] if a.window else (a.start_month, a.months))

    yr_start = yr_end = None
    if a.years:
        lo, _, hi = a.years.partition(":")
        yr_start = int(lo) if lo else None
        yr_end = int(hi) if hi else None

    band = None if a.band.lower() == "none" else tuple(float(x) for x in a.band.split(","))

    hist = data_lib.load_history(a.ticker, force_refresh=a.refresh)
    close = hist["close"]
    last_year = int(close.index[-1].year)

    res = compute_seasonality(
        close, ticker=a.ticker, start_month=start_month, n_months=months,
        year_start=yr_start, year_end=yr_end, phases=a.phase,
        aggregation="median" if a.median else "mean", band=band,
        current_year=None if a.no_current else last_year,
    )

    name = asset_lib.label_for(a.ticker)
    fig = chart_lib.build_figure(
        res,
        title=f"{name} — {res.window_name} seasonality",
        subtitle=(f"{'Median' if a.median else 'Average'} of {res.stats_all.n_years} years "
                  f"({min(res.stats_all.years)}–{max(res.stats_all.years)}), indexed to window start"),
        today=dt.date.today(),
        day_ticks=not a.no_day_ticks,
        show_all_years=not a.only_filtered,
    )

    if a.out.lower().endswith(".png"):
        fig.write_image(a.out, width=1200, height=620, scale=2)
    else:
        fig.write_html(a.out, include_plotlyjs="cdn")

    print(f"{name} ({a.ticker}) · {res.window_name}")
    print(f"  all years   n={res.stats_all.n_years:3d}  avg {res.stats_all.avg_return_pct:+.2f}%"
          f"  positive {res.stats_all.pct_positive:.0f}%")
    if res.stats_filtered is not None:
        s = res.stats_filtered
        print(f"  {res.filter_name:<12} n={s.n_years:3d}  avg {s.avg_return_pct:+.2f}%"
              f"  positive {s.pct_positive:.0f}%  low {s.low_date.strftime('%b %d')}")
    print(f"  written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
