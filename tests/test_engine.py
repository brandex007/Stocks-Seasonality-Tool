"""Unit tests for the seasonal composite engine (no network required)."""

import numpy as np
import pandas as pd
import pytest

from seasonality.engine import (
    build_matrix,
    compute_seasonality,
    cycle_phase,
    filter_years,
    monthly_returns_table,
    window_calendar,
)


def synthetic_close(start="1990-01-01", end="2025-12-31", drift=0.0002, seed=7):
    idx = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.008, len(idx))
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


def test_cycle_phase_labels():
    assert cycle_phase(2022) == "Midterm year"
    assert cycle_phase(2026) == "Midterm year"
    assert cycle_phase(2024) == "Election year"
    assert cycle_phase(2025) == "Post-election year"
    assert cycle_phase(2023) == "Pre-election year"


def test_filter_years_midterm():
    assert filter_years(range(2018, 2027), ["Midterm year"]) == [2018, 2022, 2026]


def test_window_calendar_lengths():
    assert len(window_calendar(1, 12)) == 365
    assert len(window_calendar(7, 6)) == 184  # Jul 1 – Dec 31
    assert len(window_calendar(10, 3)) == 92  # Oct 1 – Dec 31


def test_window_calendar_wraps_year_end():
    cal = window_calendar(11, 3)  # Nov – Jan
    assert (cal[0].month, cal[0].day) == (11, 1)
    assert (cal[-1].month, cal[-1].day) == (1, 31)


def test_matrix_is_indexed_to_100():
    close = synthetic_close()
    m, cal = build_matrix(close, 7, 6)
    assert len(m) == len(cal) == 184
    firsts = m.apply(lambda c: c.dropna().iloc[0])
    assert np.allclose(firsts.values, 100.0)
    assert m.notna().sum().min() > 150  # weekends forward-filled


def test_known_linear_series_gives_exact_composite():
    """A series that rises 1% per calendar day compounding must land where math says."""
    idx = pd.date_range("2000-01-01", "2004-12-31", freq="D")
    vals = 100 * (1.001 ** np.arange(len(idx)))
    close = pd.Series(vals, index=idx)
    m, cal = build_matrix(close, 7, 6)
    expected_final = 1.001 ** (len(cal) - 1) * 100
    assert m.iloc[-1].mean() == pytest.approx(expected_final, rel=1e-9)


def test_leap_day_dropped_and_alignment_stable():
    close = synthetic_close("1996-01-01", "2024-12-31")
    m, cal = build_matrix(close, 1, 12)
    assert len(cal) == 365
    assert m.shape[1] >= 25
    # every year aligns to the same grid length
    assert m.index.max() == 364


def test_compute_seasonality_midterm_split():
    close = synthetic_close()
    res = compute_seasonality(
        close, ticker="TEST", start_month=7, n_months=6,
        phases=["Midterm year"], current_year=2025, band=(25.0, 75.0),
    )
    assert res.composite_filtered is not None
    assert all(y % 4 == 2 for y in res.stats_filtered.years)
    assert set(res.stats_filtered.years).issubset(set(res.stats_all.years))
    assert res.band_low is not None and res.band_high is not None
    assert (res.band_high.dropna() >= res.band_low.dropna()).all()
    assert res.current_path is not None
    frame = res.to_frame()
    assert len(frame) == len(res.calendar)
    assert "all_years" in frame.columns


def test_partial_current_year_is_not_flatlined():
    close = synthetic_close(end="2025-09-05")
    res = compute_seasonality(close, start_month=7, n_months=6, current_year=2025)
    cur = res.current_path
    assert cur is not None
    assert cur.dropna().index.max() < len(res.calendar) - 1  # stops at last real datapoint


def test_incomplete_years_excluded_from_composite():
    close = synthetic_close(start="2000-09-01", end="2005-12-31")
    m, _ = build_matrix(close, 1, 12)
    assert 2000 not in m.columns  # only Sep onward exists
    assert 2001 in m.columns


def test_month_axis_spans_and_day_ticks():
    from seasonality.chart import _day_ticks_for, month_spans

    cal_q = window_calendar(10, 3)
    spans = month_spans(cal_q)
    assert [s[0].strftime("%b") for s in spans] == ["Oct", "Nov", "Dec"]
    assert spans[0][0].day == 1 and spans[0][1].day == 31          # Oct 1 → Oct 31
    assert _day_ticks_for(cal_q) == [1, 5, 10, 15, 20, 25]

    assert _day_ticks_for(window_calendar(7, 6)) == [1, 10, 20]
    assert _day_ticks_for(window_calendar(1, 12)) is None           # month names only

    wrap = month_spans(window_calendar(11, 3))                      # Nov → Jan
    assert [s[0].strftime("%b") for s in wrap] == ["Nov", "Dec", "Jan"]


def test_figure_has_month_labels():
    from seasonality.chart import build_figure

    close = synthetic_close()
    res = compute_seasonality(close, "TEST", 7, 6, phases=["Midterm year"])
    fig = build_figure(res)
    labels = [a.text for a in fig.layout.annotations]
    for month in ("Jul", "Aug", "Sep", "Oct", "Nov", "Dec"):
        assert f"<b>{month}</b>" in labels
    # day-of-month numbers are drawn as their own row
    assert {"1", "10", "20"}.issubset(set(labels))

    # array ticks are what made plotly's unified hover print "undefined",
    # and native tick labels would duplicate the annotation rows
    assert fig.layout.xaxis.tickmode != "array"
    assert fig.layout.xaxis.showticklabels is False
    assert fig.layout.xaxis.hoverformat == "%b %d"

    # month names only on a 12-month window
    year_fig = build_figure(compute_seasonality(close, "TEST", 1, 12))
    year_labels = [a.text for a in year_fig.layout.annotations]
    assert "<b>Mar</b>" in year_labels
    assert "15" not in year_labels


def test_monthly_returns_table_shape():
    close = synthetic_close("2010-01-01", "2020-12-31")
    t = monthly_returns_table(close, 2011, 2020)
    assert t.shape == (10, 12)
    assert list(t.columns)[:3] == ["Jan", "Feb", "Mar"]


def test_all_years_line_can_be_hidden():
    from seasonality.chart import build_figure

    close = synthetic_close()
    res = compute_seasonality(close, "TEST", 7, 6, phases=["Midterm year"])

    hidden = build_figure(res, show_all_years=False)
    names = [t.name for t in hidden.data if t.name]
    assert "All years" not in names
    assert "Midterm year" in names
    assert "+" in " ".join(a.text for a in hidden.layout.annotations)  # end label still drawn

    shown = build_figure(res, show_all_years=True)
    assert "All years" in [t.name for t in shown.data if t.name]


def test_all_years_line_survives_without_a_filter():
    """With nothing else to plot, hiding the all-years line is ignored."""
    from seasonality.chart import build_figure

    res = compute_seasonality(synthetic_close(), "TEST", 7, 6, phases=None)
    fig = build_figure(res, show_all_years=False)
    assert "All years" in [t.name for t in fig.data if t.name]


def test_title_is_never_none():
    """Streamlit wraps layout.title.text in <b>...</b> on the frontend, so a
    missing title renders as a bold 'undefined' above the chart."""
    from seasonality.chart import build_figure

    res = compute_seasonality(synthetic_close(), "TEST", 7, 6)
    untitled = build_figure(res)
    assert untitled.layout.title.text == ""

    titled = build_figure(res, title="VIX", subtitle="1990–2025")
    assert "VIX" in titled.layout.title.text


def test_y_axis_has_no_rotated_title():
    """A rotated y-axis title overlapped the tick numbers on narrow screens."""
    from seasonality.chart import build_figure

    fig = build_figure(compute_seasonality(synthetic_close(), "TEST", 7, 6))
    assert not fig.layout.yaxis.title.text
    assert fig.layout.yaxis.automargin is True


def test_band_is_skipped_when_too_few_years():
    """A 25-75 band over two years is just those two years, and it hugged the
    composite so tightly it read as a rendering artefact."""
    close = synthetic_close("2015-01-01", "2025-12-31")
    res = compute_seasonality(close, "TEST", 7, 6, phases=["Midterm year"],
                              band=(25.0, 75.0))
    assert res.stats_filtered.n_years < 5          # 2018, 2022 only
    assert res.band_low is None and res.band_high is None
    assert res.band_labels is None
    assert any("Percentile band hidden" in n for n in res.notes)

    # the same request against all years has enough history to be meaningful
    wide = compute_seasonality(close, "TEST", 7, 6, phases=["Midterm year"],
                               band=(25.0, 75.0), band_source="all")
    assert wide.band_low is not None
    assert wide.band_source_name == "all years"
    assert (wide.band_high.dropna() >= wide.band_low.dropna()).all()


def test_band_source_follows_the_filter_by_default():
    close = synthetic_close("1960-01-01", "2025-12-31")
    res = compute_seasonality(close, "TEST", 7, 6, phases=["Midterm year"],
                              band=(25.0, 75.0))
    assert res.band_source_name == "Midterm year"
    assert res.stats_filtered.n_years >= 5

    from seasonality.chart import build_figure
    names = [t.name for t in build_figure(res).data if t.name]
    assert any("midterm year" in n for n in names)  # legend says what it covers


def test_today_and_election_markers_name_their_date():
    """Month names sit at month midpoints, so a bare marker line between two of
    them reads as the wrong month. The label spells the date out."""
    import datetime as dt

    from seasonality.chart import build_figure, cal_position

    res = compute_seasonality(synthetic_close(end="2026-09-04"), "TEST", 1, 12)
    today = dt.date(2026, 9, 5)
    fig = build_figure(res, today=today)
    labels = [a.text for a in fig.layout.annotations]
    assert "you are here · Sep 5" in labels
    assert "Election Day · Nov 3" in labels          # first Tue after first Mon

    # and the line really is inside September, not August
    cal = res.calendar
    slot = cal_position(cal, 9, 5)
    assert cal[slot].month == 9
    sept = [i for i, d in enumerate(cal) if d.month == 9]
    assert sept[0] <= slot <= sept[-1]


def test_y_range_covers_every_plotted_series():
    """The live-year path can tower over the composites; autorange was leaving
    it clipped at the top edge."""
    from seasonality.chart import build_figure

    res = compute_seasonality(
        synthetic_close(end="2026-09-04"), "TEST", 1, 12,
        phases=["Midterm year"], band=(25.0, 75.0), current_year=2026,
    )
    res.current_path = res.current_path * 1.35        # a runaway year
    lo, hi = build_figure(res).layout.yaxis.range

    for series in (res.composite_all, res.composite_filtered,
                   res.band_low, res.band_high, res.current_path):
        s = series.dropna()
        assert hi > s.max(), "series clipped at the top"
        assert lo < s.min(), "series clipped at the bottom"
    assert lo < 100 < hi                              # the baseline is in view
    # more room above than below, because the legend sits inside the plot
    assert hi - res.current_path.max() > res.band_low.min() - lo


def test_y_range_includes_faint_year_lines_when_shown():
    from seasonality.chart import build_figure

    res = compute_seasonality(synthetic_close(), "TEST", 1, 12, phases=["Midterm year"])
    lo, hi = build_figure(res, show_individual_years=True).layout.yaxis.range
    assert hi > res.matrix_filtered.max().max()
    assert lo < res.matrix_filtered.min().min()
