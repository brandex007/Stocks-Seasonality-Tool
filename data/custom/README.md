# Custom price CSVs

Drop a daily price file here and the app will use it as the long-history source
for that ticker, splicing Yahoo's current data onto the end.

**Filename** = the ticker, with `=` written as `_` if your OS dislikes it:

    GC_F.csv     ->  GC=F   (gold futures)
    SI_F.csv     ->  SI=F   (silver)
    ^GSPC.csv    ->  ^GSPC

**Columns**: one date column and one price column. The reader accepts the usual
spellings — `Date`, `Observation_date`, `Time`; `Close`, `Price`, `Value`, `Last`,
`Adj Close`, `USD (AM)`, `Settle` — in any order, either sort direction, with
thousands separators and currency symbols. Everything else is ignored.

    Date,USD (AM)
    1968-04-01,38.00
    1968-04-02,38.10

**Where to get pre-2000 gold and silver**: the LBMA publishes its full daily
precious-metal price history (gold from 1968, silver from 1968) as a download on
its own site. Any daily close series works — a broker export or a Kaggle dataset
is fine too.

After adding or editing a file, hit **↻ Refresh price data** in the app: the
loader caches for six hours and won't notice the new file until you do.

Files here are ignored by git.
