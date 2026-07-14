"""
Entry logic: trade only when ribbon trend direction and gamma pin location
agree. When they disagree, wait for the trend to flip rather than fight it.

    Uptrend   + pin ABOVE spot -> CALL  (ride trend into the pin)
    Downtrend + pin BELOW spot -> PUT   (ride trend into the pin)
    Downtrend + pin ABOVE spot -> NO_TRADE, waiting for flip to uptrend
    Uptrend   + pin BELOW spot -> NO_TRADE, waiting for flip to downtrend

Gamma regime (positive/negative net GEX) is NOT a gate right now - just a
label attached to the decision so you can watch how it correlates before
deciding whether/how to weight it (e.g. holding positive-gamma trades
longer, per your note that they take longer to build).
"""

from dataclasses import dataclass
from gex_engine import StrikeGEX, find_pin_zone, find_zero_gamma_flip
from trend import TrendResult
import config


@dataclass
class TradeSignal:
    action: str          # "BUY_CALL", "BUY_PUT", or "NO_TRADE"
    ticker: str
    strike: float | None
    reason: str
    pin_strike: float | None
    net_gex_at_pin: float | None
    zero_gamma_level: float | None
    trend_direction: str
    gamma_regime: str    # "positive" or "negative" - label only, not a gate


def strike_step_for(ticker: str) -> float:
    return config.TICKER_META.get(ticker, {}).get("strike_step", 1.0)


def build_signal(ticker: str, spot_price: float, profile: list[StrikeGEX],
                  trend: TrendResult) -> TradeSignal:
    pin = find_pin_zone(profile)
    zero_gamma = find_zero_gamma_flip(profile)
    gamma_regime = "positive" if (pin and pin.net_gex > 0) else "negative"

    if pin is None:
        return TradeSignal("NO_TRADE", ticker, None, "No GEX profile available",
                            None, None, zero_gamma, trend.direction, gamma_regime)

    threshold = config.TICKER_META.get(ticker, {}).get("min_pin_gex_threshold", 100_000_000)
    if abs(pin.net_gex) < threshold:
        return TradeSignal(
            "NO_TRADE", ticker, None,
            f"Pin strike {pin.strike} GEX ({pin.net_gex:,.0f}) below threshold "
            f"({threshold:,.0f}) - not a real pin, just noise",
            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime,
        )

    if trend.direction == "flat":
        return TradeSignal(
            "NO_TRADE", ticker, None, "Insufficient bar data to classify trend",
            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime,
        )

    step = strike_step_for(ticker)
    offset = config.OTM_STRIKE_OFFSET * step
    pin_above = pin.strike > spot_price
    pin_below = pin.strike < spot_price

    if trend.direction == "up" and pin_above:
        strike = round((spot_price + offset) / step) * step
        return TradeSignal("BUY_CALL", ticker, strike,
                            f"Uptrend (EMA8 {trend.ema_fast:.2f} >= EMA21 {trend.ema_slow:.2f}) "
                            f"with pin at {pin.strike} above spot - riding trend into pin",
                            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime)

    if trend.direction == "down" and pin_below:
        strike = round((spot_price - offset) / step) * step
        return TradeSignal("BUY_PUT", ticker, strike,
                            f"Downtrend (EMA8 {trend.ema_fast:.2f} < EMA21 {trend.ema_slow:.2f}) "
                            f"with pin at {pin.strike} below spot - riding trend into pin",
                            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime)

    if trend.direction == "down" and pin_above:
        return TradeSignal("NO_TRADE", ticker, None,
                            f"Downtrend but pin at {pin.strike} is ABOVE spot {spot_price} - "
                            f"trend and pin disagree. Waiting for flip to uptrend before calling.",
                            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime)

    if trend.direction == "up" and pin_below:
        return TradeSignal("NO_TRADE", ticker, None,
                            f"Uptrend but pin at {pin.strike} is BELOW spot {spot_price} - "
                            f"trend and pin disagree. Waiting for flip to downtrend before putting.",
                            pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime)

    return TradeSignal("NO_TRADE", ticker, None,
                        f"Spot is sitting on the pin strike ({pin.strike}) - no clear direction",
                        pin.strike, pin.net_gex, zero_gamma, trend.direction, gamma_regime)
