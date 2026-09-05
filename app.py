"""Seasonality Explorer — Streamlit UI.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from seasonality import assets as asset_lib
from seasonality import chart as chart_lib
from seasonality import data as data_lib
from seasonality.engine import (
    CYCLE_PHASES,
    MONTH_NAMES,
    WINDOW_PRESETS,
    compute_seasonality,
    cycle_phase,
    monthly_returns_table,
    window_label,
)

st.set_page_config(page_title="Seasonality Explorer", page_icon="📈", layout="wide")

MUTED = chart_lib.MUTED


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def get_history(ticker: str, source: str = "auto", nonce: int = 0) -> pd.DataFrame:
    return data_lib.load_history(ticker, force_refresh=bool(nonce), source=source)


# ------------------------------------------------------------------------ sidebar

st.sidebar.title("📈 Seasonality Explorer")

st.sidebar.subheader("Asset")
category = st.sidebar.selectbox("Category", list(asset_lib.PRESETS.keys()), index=0)
choice = st.sidebar.selectbox("Asset", list(asset_lib.PRESETS[category].keys()), index=0)
preset_ticker = asset_lib.PRESETS[category][choice]

custom = st.sidebar.text_input(
    "…or type any Yahoo ticker", value="", placeholder="e.g. NVDA, BTC-USD, CL=F"
).strip()
ticker = custom.upper() if custom else preset_ticker

source = st.sidebar.radio(
    "Data source", list(data_lib.SOURCES), horizontal=True, index=0,
    format_func=lambda s: {
        "auto": "Auto", "yahoo": "Yahoo", "fred": "FRED", "custom": "CSV",
    }[s],
    help=(
        "Auto puts the longest available history in front of Yahoo's current "
        "series, splicing where they meet. Yahoo's continuous futures all begin "
        "around 2000; FRED carries WTI from 1986 and Brent from 1987; a CSV you "
        "drop in data/custom/ outranks both."
    ),
)

refresh = st.sidebar.button("↻ Refresh price data", width="stretch")
if refresh:
    st.cache_data.clear()
    data_lib.clear_cache(ticker)
    data_lib.reset_failures()

st.sidebar.subheader("Window")
window_mode = st.sidebar.radio(
    "Window type", ["Preset", "Custom"], horizontal=True, label_visibility="collapsed"
)
if window_mode == "Preset":
    preset_name = st.sidebar.selectbox(
        "Period",
        list(WINDOW_PRESETS.keys()),
        index=list(WINDOW_PRESETS).index("H2 (Jul–Dec)"),
    )
    start_month, n_months = WINDOW_PRESETS[preset_name]
else:
    start_month = MONTH_NAMES.index(st.sidebar.selectbox("Starts in", MONTH_NAMES, index=6)) + 1
    n_months = st.sidebar.select_slider("Length (months)", options=[3, 6, 12], value=3)

# ------------------------------------------------------------------- load history

try:
    with st.spinner(f"Loading {ticker}…"):
        hist = get_history(ticker, source, nonce=1 if refresh else 0)
except data_lib.DataError as exc:
    st.error(str(exc))
    st.stop()

close = hist["close"]
first_year, last_year = int(close.index[0].year), int(close.index[-1].year)
today = dt.date.today()

st.sidebar.caption(
    f"{data_lib.source_label(hist)}  \n"
    f"{close.index[0]:%Y-%m-%d} → {close.index[-1]:%Y-%m-%d} · {len(close):,} days"
)
fallback = hist.attrs.get("fallback_reason")
if fallback:
    st.sidebar.caption(f"⚠︎ {fallback}")

_custom = data_lib.custom_tickers()
if _custom:
    st.sidebar.caption("Custom CSVs: " + ", ".join(_custom))
else:
    st.sidebar.caption(
        "Longer history? Drop a daily CSV in `data/custom/` named for the ticker "
        "(e.g. `GC_F.csv`) and it is spliced in front of Yahoo."
    )

st.sidebar.subheader("Years")
yr_start, yr_end = st.sidebar.slider(
    "Year range",
    min_value=first_year,
    max_value=last_year,
    value=(first_year, last_year),
    label_visibility="collapsed",
)

st.sidebar.subheader("Overlays")
use_filter = st.sidebar.checkbox("Election-cycle filter", value=True)
phases = None
if use_filter:
    phases = st.sidebar.multiselect(
        "Include years that are", list(CYCLE_PHASES.keys()), default=["Midterm year"]
    ) or None

show_all_years = st.sidebar.checkbox(
    "Show all-years line", value=True,
    help="Turn off to chart only the filtered years (needs a cycle filter selected).",
)
show_current = st.sidebar.checkbox(f"Overlay {last_year} (current path)", value=True)
show_band = st.sidebar.checkbox("Percentile band", value=True)
band = None
band_source = "auto"
if show_band:
    lo, hi = st.sidebar.slider("Band percentiles", 5, 95, (25, 75), step=5)
    band = (float(lo), float(hi))
    if phases:
        band_source = st.sidebar.radio(
            "Band covers", ["filtered", "all"], horizontal=True, index=0,
            format_func=lambda s: "Filtered years" if s == "filtered" else "All years",
            help="Which set of years the percentile band is measured across.",
        )
show_years = st.sidebar.checkbox("Show individual years (faint)", value=False)
show_election = st.sidebar.checkbox("Mark US Election Day", value=True)
show_today = st.sidebar.checkbox("Mark 'you are here'", value=True)
day_ticks = st.sidebar.checkbox("Day-of-month ticks", value=True)
aggregation = st.sidebar.radio("Average using", ["mean", "median"], horizontal=True, index=0)

# ------------------------------------------------------------------------ compute

try:
    res = compute_seasonality(
        close,
        ticker=ticker,
        start_month=start_month,
        n_months=n_months,
        year_start=yr_start,
        year_end=yr_end,
        phases=phases,
        aggregation=aggregation,
        band=band,
        band_source=band_source,
        current_year=last_year if show_current else None,
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

cal = res.calendar
name = asset_lib.label_for(ticker)
title = f"{name} — {res.window_name} seasonality"
subtitle = (
    f"{'Median' if aggregation == 'median' else 'Average'} of {res.stats_all.n_years} years "
    f"({min(res.stats_all.years)}–{max(res.stats_all.years)}), indexed to 100 at window start · {ticker}"
)

# --------------------------------------------------------------------------- head

st.markdown(
    f"### {title}\n<span style='color:{MUTED}'>{subtitle}</span>", unsafe_allow_html=True
)
for note in res.notes:
    st.info(note)

cols = st.columns(4)
sa = res.stats_all
cols[0].metric("All-years avg return", f"{sa.avg_return_pct:+.2f}%", help=f"{sa.n_years} years")
cols[1].metric("Positive years", f"{sa.pct_positive:.0f}%")
if res.stats_filtered is not None:
    sf = res.stats_filtered
    cols[2].metric(f"{res.filter_name} avg", f"{sf.avg_return_pct:+.2f}%", help=f"{sf.n_years} years")
    cols[3].metric(
        "Seasonal low",
        sf.low_date.strftime("%b %d") if sf.low_date is not None else "—",
        help=f"Low of the {res.filter_name.lower()} composite",
    )
else:
    cols[2].metric("Best year", f"{sa.best_year} ({sa.best_return_pct:+.1f}%)")
    cols[3].metric("Worst year", f"{sa.worst_year} ({sa.worst_return_pct:+.1f}%)")

# -------------------------------------------------------------------------- chart

fig = chart_lib.build_figure(
    res,
    show_individual_years=show_years,
    show_all_years=show_all_years,
    show_election_day=show_election,
    show_today=show_today,
    today=today,
    day_ticks=day_ticks,
)
# theme=None: the figure carries its own styling, and Streamlit's plotly theme
# rewrites the title on the frontend (a missing one becomes a bold "undefined").
st.plotly_chart(fig, width="stretch", theme=None)

# --------------------------------------------------------------------- detail tabs

tab_years, tab_months, tab_export = st.tabs(["Year detail", "Monthly returns", "Export"])

with tab_years:
    rows = []
    for y in res.matrix_all.columns:
        col = res.matrix_all[y].dropna()
        if col.empty:
            continue
        rows.append(
            {
                "Year": int(y),
                "Cycle phase": cycle_phase(int(y)),
                "Window return %": round(col.iloc[-1] - 100.0, 2),
                "Max gain %": round(col.max() - 100.0, 2),
                "Max drawdown %": round(col.min() - 100.0, 2),
            }
        )
    year_df = pd.DataFrame(rows).set_index("Year").sort_index(ascending=False)
    st.dataframe(year_df, width="stretch", height=340)

    summary = []
    for phase in CYCLE_PHASES:
        sub = year_df[year_df["Cycle phase"] == phase]["Window return %"]
        if len(sub):
            summary.append(
                {
                    "Cycle phase": phase,
                    "Years": len(sub),
                    "Avg %": round(sub.mean(), 2),
                    "Median %": round(sub.median(), 2),
                    "% positive": round((sub > 0).mean() * 100, 1),
                }
            )
    st.markdown(f"**By election-cycle phase** — {res.window_name}")
    st.dataframe(pd.DataFrame(summary).set_index("Cycle phase"), width="stretch")

with tab_months:
    mt = monthly_returns_table(close, yr_start, yr_end)
    if mt.empty:
        st.info("Not enough history for a monthly table.")
    else:
        st.markdown("**Average monthly return (%)**")
        st.dataframe(mt.mean(numeric_only=True).to_frame("Average %").T.round(2), width="stretch")
        st.markdown("**Monthly returns by year (%)**")
        table = mt.sort_index(ascending=False).round(2)
        try:  # heat-map shading needs matplotlib
            table = table.style.background_gradient(cmap="RdYlGn", axis=None)
        except Exception:
            pass
        st.dataframe(table, width="stretch", height=380)

with tab_export:
    curves = res.to_frame().round(4)
    slug = f"{ticker.replace('^', '').replace('=', '')}_{start_month:02d}m{n_months}"
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "⬇︎ Plotted curves (CSV)",
        curves.to_csv().encode(),
        file_name=f"{slug}_seasonality.csv",
        mime="text/csv",
        width="stretch",
    )
    c2.download_button(
        "⬇︎ Per-year paths (CSV)",
        res.matrix_all.set_index(cal).round(4).to_csv().encode(),
        file_name=f"{slug}_per_year_paths.csv",
        mime="text/csv",
        width="stretch",
    )
    c3.download_button(
        "⬇︎ Chart (HTML)",
        fig.to_html(include_plotlyjs="cdn").encode(),
        file_name=f"{slug}_chart.html",
        mime="text/html",
        width="stretch",
    )
    st.dataframe(curves.tail(15), width="stretch")

st.caption(
    f"Prices from {data_lib.source_label(hist)}, cached locally under data/cache. "
    "Each year is indexed to 100 on the first trading day of the window, then averaged across "
    "years on a calendar-day grid. Past seasonality is a statistical tendency, not a forecast."
)
