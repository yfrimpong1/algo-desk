"""Technical indicators — pure functions over a canonical OHLCV frame.

Why hand-rolled (not pandas-ta)?
  * You are on pandas 3.0; the popular `pandas-ta` imports symbols removed from modern
    numpy/pandas and crashes on import.
  * These indicators are ~60 lines, fully transparent, dependency-free, and easy to test —
    exactly the deterministic core we want OUTSIDE the LLM (free, instant, reproducible).

Every function takes a pandas Series (usually `bars["close"]`) or the full bars frame and
returns a Series aligned to the same index. NaNs appear in the warm-up window (e.g. the
first `n-1` bars of an n-period average) — that is correct, not a bug.

Convention: callers pass the canonical frame from datafeed.get_bars()
(columns: open, high, low, close, volume; UTC DatetimeIndex).
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average: the unweighted mean of the last n values."""
    return series.rolling(window=n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """Exponential moving average: weights recent values more, so it reacts faster."""
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder), 0..100. >70 overbought, <30 oversold.

    Built from the average gain vs average loss over n bars, using Wilder's smoothing
    (an EMA with alpha = 1/n).
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD = EMA(fast) - EMA(slow); signal = EMA(MACD); hist = MACD - signal.

    Returns a DataFrame with columns [macd, signal, hist]. A rising histogram crossing
    zero from below is a classic bullish momentum trigger.
    """
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def atr(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range — volatility in price units (Wilder smoothing).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|). ATR is the smoothed
    average of TR. We use it in Phase 5 to size positions and stop-losses so that each
    trade risks the same *dollar* amount regardless of how jumpy the symbol is.
    """
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def bollinger(series: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: an SMA midline with bands k standard deviations away.

    Returns columns [mid, upper, lower]. Price riding the upper band = strong trend or
    overextension; a squeeze (narrow bands) often precedes a big move.
    """
    mid = sma(series, n)
    sd = series.rolling(window=n, min_periods=n).std()
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def add_indicators(
    bars: pd.DataFrame,
    *,
    fast: int = 20,
    slow: int = 50,
    rsi_n: int = 14,
    atr_n: int = 14,
) -> pd.DataFrame:
    """Return a copy of `bars` with the standard indicator columns appended.

    Columns added: sma_fast, sma_slow, ema_fast, rsi, atr, macd, macd_signal, macd_hist.
    This is the one call agents' data summaries and the backtester both build on.
    """
    out = bars.copy()
    close = out["close"]
    out[f"sma_{fast}"] = sma(close, fast)
    out[f"sma_{slow}"] = sma(close, slow)
    out[f"ema_{fast}"] = ema(close, fast)
    out["rsi"] = rsi(close, rsi_n)
    out["atr"] = atr(out, atr_n)
    m = macd(close)
    out["macd"] = m["macd"]
    out["macd_signal"] = m["signal"]
    out["macd_hist"] = m["hist"]
    return out
