# Where these files came from

| file | series | rows | span |
|---|---|---|---|
| `GC_F.csv` | LBMA Gold PM fix, USD/oz | 14,544 | 1968-04-01 → 2026-02-25 |
| `SI_F.csv` | LBMA Silver fix, USD/oz | 14,687 | 1968-01-02 → 2026-02-25 |

Fetched from the public dataset repo
[`unbalancedparentheses/forex-centuries`](https://github.com/unbalancedparentheses/forex-centuries)
(`data/sources/lbma/`), which compiles the LBMA's own daily precious-metal fixes.
The LBMA is the underlying source; this is a mirror of it.

## What was changed

Nothing except one row. Silver had a single price on **1983-02-05** — a Saturday,
when no fix takes place — reading 7.54 against ~14.1 on the surrounding days. It
was dropped as a data artefact. Gold needed no edits.

A rolling-median spike filter was tried and abandoned: it flagged the genuine
1979-09-18 (+37%) and 1980-03 silver moves, so no smoothing is applied. Every
remaining outlier is a real event — the January 1980 gold spike, the Hunt
brothers' silver squeeze, the April 1987 silver run.

## How they were checked before being written here

1. **Known fixes**, matched to the cent: gold $850.00 on 1980-01-21 (the peak),
   $252.80 on 1999-07-20 (the low), $1,895.00 on 2011-09-05; silver $49.45 on
   1980-01-18 (the Hunt peak).
2. **An independent monthly series** ([`datasets/gold-prices`](https://github.com/datasets/gold-prices),
   1833-) compared against these daily files aggregated to monthly means: 695
   months, correlation 0.999992, median difference 0.065%.
3. **The overlap with your own Yahoo `GC=F` cache**, 6,233 shared days from 2000:
   median price ratio 1.0002, 5th-95th percentile 0.991-1.009. Futures and the
   London fix sit on top of each other, as they should.

Daily *return* correlation over that overlap is 0.74 rather than ~1.0, which is
expected and not a fault: the PM fix is a 15:00 London snapshot while Yahoo's
close is around 18:30 London, so the two measure different 24-hour windows. Both
describe the same market; they just cut the day in different places.

## Refreshing

These files end 2026-02-25 and the app splices Yahoo onto the end, so the chart
still reaches today — no maintenance needed. To extend them, re-download from the
same repo or from the LBMA directly and overwrite the file, then hit
**↻ Refresh price data**.
