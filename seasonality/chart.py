"""Plotly figure construction for a SeasonalResult.

Kept separate from the Streamlit app so charts can also be produced from
scripts or notebooks:

    from seasonality import data, chart
    from seasonality.engine import compute_seasonality
    h = data.load_history("^GSPC")
    res = compute_seasonality(h["close"], "^GSPC", 7, 6, phases=["Midterm year"])
    chart.build_figure(res).write_image("sp500_h2.png", scale=2)
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go

INK = "#111827"      # all-years composite
ACCENT = "#2E86DE"   # filtered composite (e.g. midterm years)
BAND = "rgba(46,134,222,0.13)"
CURRENT = "#E8833A"  # live-year path
GRID = "#E8ECF1"
MUTED = "#8A94A6"
FAINT = "rgba(120,132,150,0.22)"


def election_day(year: int) -> dt.date:
    """First Tuesday after the first Monday in November."""
    d = dt.date(year, 11, 1)
    while d.weekday() != 0:  # Monday
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=1)


def cal_position(cal: pd.DatetimeIndex, month: int, day: int):
    for i, d in enumerate(cal):
        if d.month == month and d.day == day:
            return i
    return None


def build_figure(
    res,
    title: str | None = None,
    subtitle: str | None = None,
    show_individual_years: bool = False,
    show_all_years: bool = True,
    show_election_day: bool = True,
    show_today: bool = True,
    today: dt.date | None = None,
    day_ticks: bool = True,
    height: int = 560,
) -> go.Figure:
    cal = res.calendar
    today = today or dt.date.today()
    # hiding the all-years line only makes sense when something else is drawn
    show_all_years = show_all_years or res.composite_filtered is None
    fig = go.Figure()

    if show_individual_years:
        src = res.matrix_filtered if res.matrix_filtered is not None else res.matrix_all
        for y in src.columns:
            fig.add_trace(
                go.Scatter(
                    x=cal, y=src[y], mode="lines", name=str(y),
                    line=dict(color=FAINT, width=1),
                    hovertemplate=f"{y}: %{{y:.2f}}<extra></extra>",
                    showlegend=False,
                )
            )

    if res.band_low is not None and res.band_high is not None:
        lo_l, hi_l = res.band_labels or (25.0, 75.0)
        fig.add_trace(
            go.Scatter(x=cal, y=res.band_high, mode="lines", line=dict(width=0),
                       hoverinfo="skip", showlegend=False)
        )
        fig.add_trace(
            go.Scatter(
                x=cal, y=res.band_low, mode="lines", line=dict(width=0), fill="tonexty",
                fillcolor=BAND, hoverinfo="skip",
                name=f"{lo_l:g}–{hi_l:g}th percentile",
            )
        )

    if show_all_years:
        fig.add_trace(
            go.Scatter(
                x=cal, y=res.composite_all, mode="lines", name="All years",
                line=dict(color=INK, width=2.2),
                hovertemplate="%{x|%b %d} · All years: %{y:.2f}<extra></extra>",
            )
        )

    if res.composite_filtered is not None:
        fig.add_trace(
            go.Scatter(
                x=cal, y=res.composite_filtered, mode="lines", name=res.filter_name,
                line=dict(color=ACCENT, width=2.2),
                hovertemplate=f"%{{x|%b %d}} · {res.filter_name}: %{{y:.2f}}<extra></extra>",
            )
        )

    if res.current_path is not None:
        fig.add_trace(
            go.Scatter(
                x=cal, y=res.current_path, mode="lines", name=f"{res.current_year} actual",
                line=dict(color=CURRENT, width=2, dash="dot"),
                hovertemplate=f"%{{x|%b %d}} · {res.current_year}: %{{y:.2f}}<extra></extra>",
            )
        )

    fig.add_hline(y=100, line=dict(color="#C9D2DD", width=1))

    def end_label(series, color, yshift=0):
        s = series.dropna() if series is not None else None
        if s is None or s.empty:
            return None
        i = int(s.index[-1])
        fig.add_annotation(
            x=cal[i], y=s.iloc[-1], text=f"{s.iloc[-1] - 100:+.1f}%", showarrow=False,
            xanchor="left", xshift=6, yshift=yshift, font=dict(color="white", size=12),
            bgcolor=color, borderpad=4,
        )
        return float(s.iloc[-1])

    # nudge the two end labels apart when the composites finish close together
    all_series = res.composite_all if show_all_years else None
    ends = [all_series, res.composite_filtered]
    finals = [s.dropna().iloc[-1] for s in ends if s is not None and not s.dropna().empty]
    y_span = max(1e-9, float(res.matrix_all.max().max() - res.matrix_all.min().min()))
    crowded = len(finals) == 2 and abs(finals[0] - finals[1]) < 0.06 * y_span
    shift = 11 if crowded else 0
    if crowded:
        all_first = finals[0] >= finals[1]
        end_label(all_series, INK, shift if all_first else -shift)
        end_label(res.composite_filtered, ACCENT, -shift if all_first else shift)
    else:
        end_label(all_series, INK)
        end_label(res.composite_filtered, ACCENT)

    highlight = res.composite_filtered if res.composite_filtered is not None else res.composite_all
    hl_color = ACCENT if res.composite_filtered is not None else INK
    hs = highlight.dropna()
    if not hs.empty:
        li = int(hs.idxmin())
        fig.add_trace(
            go.Scatter(
                x=[cal[li]], y=[hs.min()], mode="markers",
                marker=dict(color=hl_color, size=9),
                hovertemplate="Seasonal low · %{x|%b %d} · %{y:.2f}<extra></extra>",
                showlegend=False,
            )
        )
        fig.add_annotation(
            x=cal[li], y=hs.min(), text=f"Low = {cal[li].strftime('%b %d')}",
            showarrow=False, yshift=-24, font=dict(size=12, color="#B45309"),
            bgcolor="#FEF3C7", bordercolor="#F59E0B", borderwidth=1, borderpad=3,
        )

    if show_election_day:
        ed = election_day(today.year)
        p = cal_position(cal, ed.month, ed.day)
        if p is not None:
            fig.add_vline(x=cal[p], line=dict(color=ACCENT, width=1.2, dash="dash"))
            fig.add_annotation(
                x=cal[p], y=1.02, yref="paper", text="Election Day", showarrow=False,
                xanchor="left", xshift=4, font=dict(size=11, color=ACCENT),
            )

    if show_today:
        p = cal_position(cal, today.month, today.day)
        if p is not None:
            fig.add_vline(x=cal[p], line=dict(color="#2E7D32", width=1.2, dash="dot"))
            fig.add_annotation(
                x=cal[p], y=0.03, yref="paper", text="you are here", showarrow=False,
                xanchor="left", xshift=4, font=dict(size=11, color="#2E7D32"),
            )

    layout_title = None
    if title:
        text = f"<b>{title}</b>"
        if subtitle:
            text += f"<br><span style='font-size:13px;color:{MUTED}'>{subtitle}</span>"
        layout_title = dict(text=text, x=0.01, xanchor="left", font=dict(size=20, color=INK))

    fig.update_layout(
        title=layout_title,
        height=height,
        margin=dict(l=10, r=96, t=96 if title else 30, b=10),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        legend=dict(
            orientation="h", x=0.01, xanchor="left", y=0.99, yanchor="top",
            bgcolor="rgba(255,255,255,0.75)", borderwidth=0,
        ),
        font=dict(family="Inter, -apple-system, Segoe UI, Helvetica, sans-serif",
                  color=INK, size=13),
    )
    pad = pd.Timedelta(days=max(2, len(cal) // 60))
    fig.update_xaxes(
        showgrid=False,
        linecolor=GRID, ticks="outside", tickcolor=GRID,
        range=[cal[0] - pad, cal[-1] + pad],
        hoverformat="%b %d",
    )
    _apply_month_axis(fig, cal, day_ticks=day_ticks)
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, zeroline=False,
        title="Index (window start = 100)", title_font=dict(color=MUTED, size=12),
    )
    return fig


def month_spans(cal: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """(first day, last day, midpoint) for every month present in the window."""
    spans = []
    keys = pd.Series(cal).dt.to_period("M")
    for _, group in pd.Series(cal).groupby(keys, sort=True):
        first, last = group.iloc[0], group.iloc[-1]
        spans.append((first, last, first + (last - first) / 2))
    return sorted(spans, key=lambda s: s[0])


def _day_ticks_for(cal: pd.DatetimeIndex) -> list[int] | None:
    """Which days of the month to tick, given how many months are on the axis."""
    n_months = len(month_spans(cal))
    if n_months <= 3:
        return [1, 5, 10, 15, 20, 25]
    if n_months <= 6:
        return [1, 10, 20]
    return None  # 12-month window: month names only


def _apply_month_axis(fig: go.Figure, cal: pd.DatetimeIndex, day_ticks: bool = True) -> None:
    """Two-tier x-axis: day-of-month numbers with month names on a row beneath.

    Both rows are drawn as annotations pinned to the bottom of the plot area with
    pixel offsets, and the axis itself keeps its native date ticks turned off.
    Using annotations rather than ``tickmode="array"`` matters: array ticks make
    plotly's unified hover label lose the date and print "undefined", and paper
    fractions drift with figure height, which clipped the month row.
    """
    spans = month_spans(cal)
    days = _day_ticks_for(cal) if day_ticks else None

    # native tick labels off — the two annotation rows below replace them
    fig.update_xaxes(tickmode="auto", showticklabels=False, ticks="", ticklen=0)

    if days:
        for first, last, _ in spans:
            month_start = pd.Timestamp(year=first.year, month=first.month, day=1)
            for day in days:
                d = month_start + pd.Timedelta(days=day - 1)
                if d.month == first.month and first <= d <= last:
                    fig.add_annotation(
                        x=d, y=0, xref="x", yref="paper", yanchor="top", yshift=-7,
                        text=str(day), showarrow=False,
                        font=dict(size=10, color=MUTED),
                    )
        month_yshift, bottom_margin = -26, 66
    else:
        month_yshift, bottom_margin = -7, 44

    for _, _, mid in spans:
        fig.add_annotation(
            x=mid, y=0, xref="x", yref="paper", yanchor="top", yshift=month_yshift,
            text=f"<b>{mid.strftime('%b')}</b>", showarrow=False,
            font=dict(size=12, color=INK),
        )

    # a light separator between months
    for first, _, _ in spans[1:]:
        fig.add_shape(
            type="line", xref="x", yref="paper", x0=first, x1=first, y0=0, y1=1,
            line=dict(color="#F1F4F8", width=1), layer="below",
        )

    fig.update_layout(margin=dict(b=bottom_margin))
