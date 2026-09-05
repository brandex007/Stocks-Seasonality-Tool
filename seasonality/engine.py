"""Seasonal composite engine.

Given a daily close series, slice it into one segment per year over a chosen
calendar window (full year, half year, quarter, or any custom start month +
length), index each segment to 100 at the window start, align every year on a
common calendar grid, and average across years.

The alignment grid is calendar-based (Jul 1, Jul 2, ...) rather than
trading-day based, so the x-axis reads as real dates and holidays/weekends are
carried forward from the previous close.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

REF_YEAR = 2001  # a non-leap year, used only for x-axis labels

WINDOW_PRESETS: dict[str, tuple[int, int]] = {
    "Full year (Jan–Dec)": (1, 12),
    "H1 (Jan–Jun)": (1, 6),
    "H2 (Jul–Dec)": (7, 6),
    "Q1 (Jan–Mar)": (1, 3),
    "Q2 (Apr–Jun)": (4, 3),
    "Q3 (Jul–Sep)": (7, 3),
    "Q4 (Oct–Dec)": (10, 3),
}

CYCLE_PHASES: dict[str, int] = {
    "Election year": 0,
    "Post-election year": 1,
    "Midterm year": 2,
    "Pre-election year": 3,
}
_PHASE_BY_MOD = {v: k for k, v in CYCLE_PHASES.items()}

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def cycle_phase(year: int) -> str:
    """US election-cycle phase for a calendar year (midterm = 2022, 2026...)."""
    return _PHASE_BY_MOD[year % 4]


def filter_years(years, phases=None) -> list[int]:
    """Keep only years whose election-cycle phase is in ``phases``."""
    years = sorted(int(y) for y in years)
    if not phases:
        return years
    wanted = {CYCLE_PHASES[p] if isinstance(p, str) else int(p) for p in phases}
    return [y for y in years if y % 4 in wanted]


def window_calendar(start_month: int, n_months: int) -> pd.DatetimeIndex:
    """Daily calendar grid for the window, labelled in a non-leap reference year."""
    start = pd.Timestamp(year=REF_YEAR, month=start_month, day=1)
    end = start + pd.DateOffset(months=n_months) - pd.Timedelta(days=1)
    return pd.date_range(start, end, freq="D")


def window_label(start_month: int, n_months: int) -> str:
    for label, (m, n) in WINDOW_PRESETS.items():
        if (m, n) == (start_month, n_months):
            return label
    end_month = ((start_month - 1 + n_months - 1) % 12) + 1
    return f"{MONTH_NAMES[start_month - 1][:3]}–{MONTH_NAMES[end_month - 1][:3]} ({n_months}m)"


def _year_bounds(year: int, start_month: int, n_months: int):
    start = pd.Timestamp(year=year, month=start_month, day=1)
    end = start + pd.DateOffset(months=n_months) - pd.Timedelta(days=1)
    return start, end


def build_matrix(
    close: pd.Series,
    start_month: int = 1,
    n_months: int = 12,
    years=None,
    min_coverage: float = 0.9,
    max_start_gap_days: int = 10,
    keep_incomplete: bool = False,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Indexed (base = 100) path per year, aligned on the window calendar.

    Returns ``(matrix, calendar)`` where ``matrix`` has one column per year and
    one row per calendar day of the window. Years that do not cover enough of
    the window are dropped unless ``keep_incomplete`` is set.
    """
    close = close.dropna().sort_index()
    if not isinstance(close.index, pd.DatetimeIndex):
        close.index = pd.to_datetime(close.index)

    cal = window_calendar(start_month, n_months)
    pos = {(d.month, d.day): i for i, d in enumerate(cal)}
    n_slots = len(cal)

    if years is None:
        first, last = close.index[0], close.index[-1]
        years = range(first.year, last.year + 1)
    years = sorted({int(y) for y in years})

    cols: dict[int, pd.Series] = {}
    for y in years:
        start, end = _year_bounds(y, start_month, n_months)
        seg = close.loc[(close.index >= start) & (close.index <= end)]
        seg = seg[~((seg.index.month == 2) & (seg.index.day == 29))]
        if seg.empty:
            continue

        slots = [pos.get((d.month, d.day)) for d in seg.index]
        s = pd.Series(np.asarray(seg.values, dtype="float64"), index=slots)
        s = s[[i is not None for i in s.index]]
        s = s.groupby(level=0).last()
        raw = s.reindex(range(n_slots))

        observed = raw.notna()
        if not observed.any():
            continue
        first_slot = int(observed.idxmax())
        last_slot = int(observed[::-1].idxmax())
        span = last_slot - first_slot + 1
        complete = (
            first_slot <= max_start_gap_days
            and span >= min_coverage * n_slots
            and observed.sum() >= min_coverage * span * 0.6
        )
        if not complete and not keep_incomplete:
            continue

        path = raw.ffill()
        base = path.iloc[first_slot]
        if not np.isfinite(base) or base == 0:
            continue
        path = path / base * 100.0
        path.iloc[:first_slot] = np.nan
        if keep_incomplete:
            # never flat-line past the last real observation (e.g. the live year)
            path.iloc[last_slot + 1 :] = np.nan
        cols[y] = path

    matrix = pd.DataFrame(cols, index=range(n_slots))
    matrix.columns = [int(c) for c in matrix.columns]
    return matrix, cal


@dataclass
class GroupStats:
    name: str
    years: list[int]
    n_years: int
    avg_return_pct: float
    median_return_pct: float
    pct_positive: float
    best_year: int | None
    best_return_pct: float
    worst_year: int | None
    worst_return_pct: float
    low_date: pd.Timestamp | None
    low_value: float
    high_date: pd.Timestamp | None
    high_value: float


@dataclass
class SeasonalResult:
    ticker: str
    calendar: pd.DatetimeIndex
    start_month: int
    n_months: int
    matrix_all: pd.DataFrame
    composite_all: pd.Series
    stats_all: GroupStats
    matrix_filtered: pd.DataFrame | None = None
    composite_filtered: pd.Series | None = None
    stats_filtered: GroupStats | None = None
    band_low: pd.Series | None = None
    band_high: pd.Series | None = None
    band_labels: tuple[float, float] | None = None
    band_source_name: str = ""
    current_year: int | None = None
    current_path: pd.Series | None = None
    filter_name: str = ""
    aggregation: str = "mean"
    notes: list[str] = field(default_factory=list)

    @property
    def window_name(self) -> str:
        return window_label(self.start_month, self.n_months)

    def to_frame(self) -> pd.DataFrame:
        """Tidy export of every plotted series, indexed by calendar date."""
        out = pd.DataFrame(index=self.calendar)
        out.index.name = "date"
        out["all_years"] = self.composite_all.values
        if self.composite_filtered is not None:
            out[self.filter_name or "filtered"] = self.composite_filtered.values
        if self.band_low is not None and self.band_high is not None:
            lo, hi = self.band_labels or (25.0, 75.0)
            out[f"p{lo:g}"] = self.band_low.values
            out[f"p{hi:g}"] = self.band_high.values
        if self.current_path is not None:
            out[str(self.current_year)] = self.current_path.values
        return out


def _aggregate(matrix: pd.DataFrame, how: str) -> pd.Series:
    if matrix.empty:
        return pd.Series(dtype="float64", index=matrix.index)
    if how == "median":
        return matrix.median(axis=1, skipna=True)
    return matrix.mean(axis=1, skipna=True)


def _group_stats(name: str, matrix: pd.DataFrame, composite: pd.Series, cal) -> GroupStats:
    if matrix.empty:
        return GroupStats(name, [], 0, np.nan, np.nan, np.nan, None, np.nan, None, np.nan,
                          None, np.nan, None, np.nan)
    finals = {}
    for y in matrix.columns:
        col = matrix[y].dropna()
        if len(col) >= 2:
            finals[int(y)] = col.iloc[-1] - 100.0
    finals_s = pd.Series(finals, dtype="float64")
    comp = composite.dropna()
    low_i = int(comp.idxmin()) if not comp.empty else None
    high_i = int(comp.idxmax()) if not comp.empty else None
    return GroupStats(
        name=name,
        years=[int(y) for y in matrix.columns],
        n_years=int(matrix.shape[1]),
        avg_return_pct=float(finals_s.mean()) if not finals_s.empty else np.nan,
        median_return_pct=float(finals_s.median()) if not finals_s.empty else np.nan,
        pct_positive=float((finals_s > 0).mean() * 100) if not finals_s.empty else np.nan,
        best_year=int(finals_s.idxmax()) if not finals_s.empty else None,
        best_return_pct=float(finals_s.max()) if not finals_s.empty else np.nan,
        worst_year=int(finals_s.idxmin()) if not finals_s.empty else None,
        worst_return_pct=float(finals_s.min()) if not finals_s.empty else np.nan,
        low_date=cal[low_i] if low_i is not None else None,
        low_value=float(comp.min()) if not comp.empty else np.nan,
        high_date=cal[high_i] if high_i is not None else None,
        high_value=float(comp.max()) if not comp.empty else np.nan,
    )


def compute_seasonality(
    close: pd.Series,
    ticker: str = "",
    start_month: int = 1,
    n_months: int = 12,
    year_start: int | None = None,
    year_end: int | None = None,
    phases=None,
    aggregation: str = "mean",
    band: tuple[float, float] | None = (25.0, 75.0),
    band_source: str = "auto",
    band_min_years: int = 5,
    current_year: int | None = None,
    min_coverage: float = 0.9,
) -> SeasonalResult:
    """Build every series the chart needs in one pass.

    ``phases`` selects the election-cycle subset drawn as the second line
    (e.g. ``["Midterm year"]``). ``current_year`` adds the live partial path.

    ``band_source`` picks which set of years the percentile band describes:
    ``"filtered"``, ``"all"``, or ``"auto"`` (the filtered set when there is one).
    A band drawn from a handful of years is just those years' spread, so it is
    skipped — with a note — when the source has fewer than ``band_min_years``.
    """
    close = close.dropna().sort_index()
    if close.empty:
        raise ValueError("Empty price series.")

    all_years = list(range(close.index[0].year, close.index[-1].year + 1))
    if year_start is not None:
        all_years = [y for y in all_years if y >= year_start]
    if year_end is not None:
        all_years = [y for y in all_years if y <= year_end]

    matrix_all, cal = build_matrix(
        close, start_month, n_months, years=all_years, min_coverage=min_coverage
    )
    notes: list[str] = []
    if matrix_all.empty:
        raise ValueError(
            "No complete years of history in this window for the selected range."
        )

    composite_all = _aggregate(matrix_all, aggregation)
    stats_all = _group_stats("All years", matrix_all, composite_all, cal)

    matrix_f = composite_f = stats_f = None
    filter_name = ""
    if phases:
        phase_list = list(phases)
        filter_name = " + ".join(phase_list) if len(phase_list) > 1 else phase_list[0]
        keep = [y for y in matrix_all.columns if y in set(filter_years(matrix_all.columns, phase_list))]
        matrix_f = matrix_all[keep] if keep else pd.DataFrame(index=matrix_all.index)
        if matrix_f.empty:
            notes.append(f"No {filter_name.lower()}s inside the selected year range.")
            matrix_f = composite_f = stats_f = None
            filter_name = ""
        else:
            composite_f = _aggregate(matrix_f, aggregation)
            stats_f = _group_stats(filter_name, matrix_f, composite_f, cal)

    band_low = band_high = None
    band_source_name = ""
    if band:
        lo_q, hi_q = band
        want_filtered = band_source in ("filtered", "auto") and matrix_f is not None
        source = matrix_f if want_filtered else matrix_all
        source_name = filter_name if want_filtered else "all years"
        n_source = int(source.shape[1])
        if n_source < band_min_years:
            notes.append(
                f"Percentile band hidden: only {n_source} "
                f"{source_name.lower()}{'' if n_source == 1 else 's'} in range, and a band "
                f"drawn from fewer than {band_min_years} is just those years' spread."
            )
        else:
            band_low = source.quantile(lo_q / 100.0, axis=1)
            band_high = source.quantile(hi_q / 100.0, axis=1)
            band_source_name = source_name
            # a row backed by one or two years is a line, not a distribution
            thin = source.notna().sum(axis=1) < band_min_years
            band_low[thin] = np.nan
            band_high[thin] = np.nan

    current_path = None
    if current_year is not None:
        cur_matrix, _ = build_matrix(
            close,
            start_month,
            n_months,
            years=[current_year],
            keep_incomplete=True,
            min_coverage=0.0,
            max_start_gap_days=45,
        )
        if not cur_matrix.empty:
            current_path = cur_matrix.iloc[:, 0]
            if current_path.dropna().empty:
                current_path = None
        if current_path is None:
            notes.append(f"No {current_year} data yet in this window.")

    return SeasonalResult(
        ticker=ticker,
        calendar=cal,
        start_month=start_month,
        n_months=n_months,
        matrix_all=matrix_all,
        composite_all=composite_all,
        stats_all=stats_all,
        matrix_filtered=matrix_f,
        composite_filtered=composite_f,
        stats_filtered=stats_f,
        band_low=band_low,
        band_high=band_high,
        band_labels=band if band_low is not None else None,
        band_source_name=band_source_name,
        current_year=current_year if current_path is not None else None,
        current_path=current_path,
        filter_name=filter_name,
        aggregation=aggregation,
        notes=notes,
    )


def monthly_returns_table(close: pd.Series, year_start=None, year_end=None) -> pd.DataFrame:
    """Calendar-month total returns (%) by year — the classic seasonality grid."""
    close = close.dropna().sort_index()
    monthly = close.resample("ME").last() if hasattr(close, "resample") else close
    rets = monthly.pct_change() * 100.0
    df = pd.DataFrame({"ret": rets})
    df["year"] = df.index.year
    df["month"] = df.index.month
    if year_start is not None:
        df = df[df["year"] >= year_start]
    if year_end is not None:
        df = df[df["year"] <= year_end]
    table = df.pivot_table(index="year", columns="month", values="ret", aggfunc="last")
    table.columns = [MONTH_NAMES[m - 1][:3] for m in table.columns]
    return table
