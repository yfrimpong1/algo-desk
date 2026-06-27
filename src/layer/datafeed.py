"""Open-source-first market data access (Phase 1).

One uniform interface over two very different vendors:

  * crypto pairs (symbol contains "/")  -> **ccxt** (default exchange: kraken)
  * equities/ETFs/FX                     -> **yfinance**

Public surface
--------------
get_quote(symbol)                         -> Quote      (latest price; from Phase 0)
get_bars(symbol, timeframe, start, end)   -> DataFrame  (canonical OHLCV history)

Canonical bar frame: a pandas DataFrame indexed by a **UTC** DatetimeIndex named
"timestamp", with float columns [open, high, low, close, volume], sorted ascending.
Crypto and equities come back identically shaped, so nothing downstream (indicators,
backtester, agents) ever branches on asset class.

Caching: historical bars are immutable, so we persist them to parquet under data/ and
serve from disk when the requested window is already covered. This makes backtests fast
and reproducible and keeps us under vendor rate limits. (Reproducibility also matters
for avoiding look-ahead bias — the same query always returns the same bars.)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import pandas as pd

from src.schemas import Quote

DateLike = Union[str, datetime, None]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CANONICAL_COLS = ["open", "high", "low", "close", "volume"]

# Reuse one ccxt client across calls (creating one per call is slow / rate-limit prone).
_ccxt_client = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _crypto_client():
    global _ccxt_client
    if _ccxt_client is None:
        import ccxt

        # Kraken serves public market data globally (Binance returns HTTP 451 in the US).
        _ccxt_client = ccxt.kraken({"enableRateLimit": True})
    return _ccxt_client


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol


def _to_utc(dt: DateLike, default: datetime) -> datetime:
    """Coerce str/datetime/None to a timezone-aware UTC datetime."""
    if dt is None:
        return default
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cache_path(symbol: str, timeframe: str) -> str:
    safe = symbol.replace("/", "-")
    return os.path.abspath(os.path.join(CACHE_DIR, f"{safe}_{timeframe}.parquet"))


# --------------------------------------------------------------------------- #
# Latest price (Phase 0)
# --------------------------------------------------------------------------- #
def get_quote(symbol: str) -> Quote:
    """Return the latest price for `symbol` as a validated Quote."""
    if _is_crypto(symbol):
        ticker = _crypto_client().fetch_ticker(symbol)
        price = float(ticker["last"])
        if ticker.get("timestamp"):
            ts = datetime.fromtimestamp(ticker["timestamp"] / 1000, tz=timezone.utc)
        else:
            ts = datetime.now(tz=timezone.utc)
        return Quote(symbol=symbol, price=price, timestamp=ts, source="ccxt:kraken")

    import yfinance as yf

    t = yf.Ticker(symbol)
    price = float(t.fast_info["last_price"])
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=datetime.now(tz=timezone.utc),
        source="yfinance",
    )


# --------------------------------------------------------------------------- #
# Historical bars (Phase 1)
# --------------------------------------------------------------------------- #
def _fetch_crypto_bars(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Page through ccxt fetch_ohlcv from `start` to `end`."""
    client = _crypto_client()
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[list] = []
    while since < end_ms:
        batch = client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=720)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= since:  # no forward progress -> stop (avoids infinite loop)
            break
        since = last_ts + 1
        if len(batch) < 720:  # exchange returned a short final page
            break
    if not rows:
        return pd.DataFrame(columns=CANONICAL_COLS)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns=["ts"]).set_index("timestamp")
    return df[CANONICAL_COLS].astype(float)


def _yf_interval(timeframe: str) -> str:
    """Map our timeframe to a yfinance interval (mostly identical)."""
    return {"1h": "60m"}.get(timeframe, timeframe)


def _fetch_equity_bars(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(symbol).history(
        start=start, end=end, interval=_yf_interval(timeframe), auto_adjust=True
    )
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLS)
    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "timestamp"
    return df[CANONICAL_COLS].astype(float)


def get_bars(
    symbol: str,
    timeframe: str = "1d",
    start: DateLike = None,
    end: DateLike = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Return canonical OHLCV bars for `symbol` between `start` and `end` (UTC).

    Defaults: last ~365 days ending now. Bars are cached to parquet per
    (symbol, timeframe); a covered request is served from disk.
    """
    end_dt = _to_utc(end, datetime.now(tz=timezone.utc))
    start_dt = _to_utc(start, end_dt - timedelta(days=365))

    path = _cache_path(symbol, timeframe)
    cached: Optional[pd.DataFrame] = None
    if use_cache and os.path.exists(path):
        cached = pd.read_parquet(path)
        # If the cache already spans the requested window, slice and return it.
        if not cached.empty and cached.index.min() <= start_dt and cached.index.max() >= end_dt:
            return cached.loc[start_dt:end_dt].copy()

    # Fetch fresh for the requested window.
    if _is_crypto(symbol):
        fetched = _fetch_crypto_bars(symbol, timeframe, start_dt, end_dt)
    else:
        fetched = _fetch_equity_bars(symbol, timeframe, start_dt, end_dt)

    # Merge with any existing cache (union of timestamps, newest fetch wins on overlap).
    if cached is not None and not cached.empty:
        merged = pd.concat([cached, fetched])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = fetched.sort_index()

    if use_cache and not merged.empty:
        os.makedirs(os.path.abspath(CACHE_DIR), exist_ok=True)
        merged.to_parquet(path)

    return merged.loc[start_dt:end_dt].copy()


if __name__ == "__main__":
    # Manual smoke test: `python -m src.layer.datafeed`
    for sym, tf in (("BTC/USD", "1d"), ("AAPL", "1d")):
        q = get_quote(sym)
        bars = get_bars(sym, tf, start="2026-01-01")
        print(
            f"{sym:9s} quote={q.price:>11,.2f} [{q.source}] | "
            f"bars={len(bars):>4d} {bars.index.min().date()}..{bars.index.max().date()} "
            f"last_close={bars['close'].iloc[-1]:,.2f}"
        )
