"""Preset assets grouped by category.

Tickers are Yahoo Finance symbols. Any symbol Yahoo knows can also be typed
directly in the app's "custom ticker" box.
"""

from collections import OrderedDict

PRESETS = OrderedDict(
    [
        (
            "Indices",
            OrderedDict(
                [
                    ("S&P 500 (^GSPC)", "^GSPC"),
                    ("Nasdaq 100 (^NDX)", "^NDX"),
                    ("Nasdaq Composite (^IXIC)", "^IXIC"),
                    ("Dow Jones (^DJI)", "^DJI"),
                    ("Russell 2000 (^RUT)", "^RUT"),
                    ("VIX (^VIX)", "^VIX"),
                    ("Nikkei 225 (^N225)", "^N225"),
                    ("DAX (^GDAXI)", "^GDAXI"),
                    ("FTSE 100 (^FTSE)", "^FTSE"),
                ]
            ),
        ),
        (
            "US ETFs",
            OrderedDict(
                [
                    ("S&P 500 ETF (SPY)", "SPY"),
                    ("Nasdaq 100 ETF (QQQ)", "QQQ"),
                    ("Russell 2000 ETF (IWM)", "IWM"),
                    ("Dow ETF (DIA)", "DIA"),
                    ("20+ Yr Treasuries (TLT)", "TLT"),
                    ("High Yield Credit (HYG)", "HYG"),
                    ("Gold ETF (GLD)", "GLD"),
                ]
            ),
        ),
        (
            "Sectors",
            OrderedDict(
                [
                    ("Technology (XLK)", "XLK"),
                    ("Financials (XLF)", "XLF"),
                    ("Energy (XLE)", "XLE"),
                    ("Health Care (XLV)", "XLV"),
                    ("Industrials (XLI)", "XLI"),
                    ("Consumer Discretionary (XLY)", "XLY"),
                    ("Consumer Staples (XLP)", "XLP"),
                    ("Utilities (XLU)", "XLU"),
                    ("Materials (XLB)", "XLB"),
                    ("Real Estate (XLRE)", "XLRE"),
                    ("Semiconductors (SMH)", "SMH"),
                ]
            ),
        ),
        (
            "Commodities",
            OrderedDict(
                [
                    ("Gold (GC=F)", "GC=F"),
                    ("Silver (SI=F)", "SI=F"),
                    ("Platinum (PL=F)", "PL=F"),
                    ("Palladium (PA=F)", "PA=F"),
                    ("Copper (HG=F)", "HG=F"),
                    ("WTI Crude (CL=F)", "CL=F"),
                    ("Brent Crude (BZ=F)", "BZ=F"),
                    ("Natural Gas (NG=F)", "NG=F"),
                    ("Gasoline (RB=F)", "RB=F"),
                    ("Heating Oil (HO=F)", "HO=F"),
                    ("Corn (ZC=F)", "ZC=F"),
                    ("Wheat (ZW=F)", "ZW=F"),
                    ("Soybeans (ZS=F)", "ZS=F"),
                    ("Coffee (KC=F)", "KC=F"),
                    ("Sugar (SB=F)", "SB=F"),
                    ("Cocoa (CC=F)", "CC=F"),
                    ("Cotton (CT=F)", "CT=F"),
                    ("Live Cattle (LE=F)", "LE=F"),
                    ("Lean Hogs (HE=F)", "HE=F"),
                ]
            ),
        ),
        (
            "FX & Rates",
            OrderedDict(
                [
                    ("US Dollar Index (DX-Y.NYB)", "DX-Y.NYB"),
                    ("EUR/USD (EURUSD=X)", "EURUSD=X"),
                    ("USD/JPY (USDJPY=X)", "USDJPY=X"),
                    ("GBP/USD (GBPUSD=X)", "GBPUSD=X"),
                    ("US 10Y Yield (^TNX)", "^TNX"),
                ]
            ),
        ),
        (
            "Crypto",
            OrderedDict(
                [
                    ("Bitcoin (BTC-USD)", "BTC-USD"),
                    ("Ethereum (ETH-USD)", "ETH-USD"),
                    ("Solana (SOL-USD)", "SOL-USD"),
                ]
            ),
        ),
        (
            "Mega-cap Stocks",
            OrderedDict(
                [
                    ("Apple (AAPL)", "AAPL"),
                    ("Microsoft (MSFT)", "MSFT"),
                    ("Nvidia (NVDA)", "NVDA"),
                    ("Amazon (AMZN)", "AMZN"),
                    ("Alphabet (GOOGL)", "GOOGL"),
                    ("Meta (META)", "META"),
                    ("Tesla (TSLA)", "TSLA"),
                ]
            ),
        ),
    ]
)


def all_presets():
    """Flat {label: ticker} mapping across every category."""
    flat = OrderedDict()
    for group in PRESETS.values():
        flat.update(group)
    return flat


def label_for(ticker: str) -> str:
    for label, sym in all_presets().items():
        if sym.upper() == ticker.upper():
            return label.split(" (")[0]
    return ticker.upper()
