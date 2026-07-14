"""
Trend classification off the same 8/21 EMA relationship that colors the
Pivot EMA in Saty's Pivot Ribbon Pro ("Pivot Bias"): pivot_bias_ema (8) vs
pivot_ema (21). This module doesn't touch TradingView - it recomputes the
same crossover from raw OHLC bars, since that's the only data path
available to a script (no TradingView API access).

Feed it minute bars from Robinhood's get_equity_historicals (or whatever
source you're pulling SPY 1m bars from) - Claude Code pulls the bars,
this module just does the math.
"""

from dataclasses import dataclass


def ema(values: list[float], length: int) -> list[float]:
    """Standard EMA, seeded with a simple average of the first `length` bars."""
    if len(values) < length:
        return []
    k = 2 / (length + 1)
    seed = sum(values[:length]) / length
    out = [seed]
    for v in values[length:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


@dataclass
class TrendResult:
    direction: str  # "up", "down", or "flat" (flat = insufficient data)
    ema_fast: float | None  # 8 EMA, most recent value
    ema_slow: float | None  # 21 EMA, most recent value


def classify_trend(closes: list[float], fast_length: int = 8, slow_length: int = 21) -> TrendResult:
    """
    closes: list of close prices, oldest first, most recent last.
    Mirrors the ribbon's pivot bias: 8 EMA >= 21 EMA -> up, else down.
    """
    fast_vals = ema(closes, fast_length)
    slow_vals = ema(closes, slow_length)
    if not fast_vals or not slow_vals:
        return TrendResult("flat", None, None)

    fast_now = fast_vals[-1]
    slow_now = slow_vals[-1]
    direction = "up" if fast_now >= slow_now else "down"
    return TrendResult(direction, fast_now, slow_now)
