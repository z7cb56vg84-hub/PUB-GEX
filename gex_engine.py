"""
GEX engine: pulls the 0DTE options chain and live quotes directly from
Robinhood via the Trading MCP (get_option_instruments + get_option_quotes),
then computes per-strike dealer gamma exposure.

No Polygon/Massive dependency - Robinhood's own quote data already returns
implied_volatility, delta, gamma, and open_interest per contract. If those
come back populated (confirm during market hours - they were null/zero in
an after-hours test), we use Robinhood's own greeks directly. If gamma is
null but implied_volatility is present, we fall back to computing gamma
ourselves via Black-Scholes off Robinhood's IV.

IMPORTANT: this module doesn't call the MCP tools itself - it's plain
Python with no network access to Robinhood. Claude Code (or whatever agent
is looped against this) is responsible for calling get_option_instruments
and get_option_quotes, then passing the results into these functions. See
main.py for the expected shape.
"""

import math
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class StrikeGEX:
    strike: float
    call_gex: float
    put_gex: float
    net_gex: float


def bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float = 0.05) -> float:
    """Black-Scholes gamma. Same for calls and puts."""
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
    pdf = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
    return pdf / (spot * iv * math.sqrt(t_years))


def build_chain_rows(instruments: list, quotes_by_id: dict) -> list:
    """
    instruments: raw list from Robinhood get_option_instruments (has id, strike_price, type).
    quotes_by_id: dict of instrument_id -> quote dict from get_option_quotes.
    Returns normalized rows: {strike, contract_type, open_interest, implied_volatility, gamma}
    Skips contracts with no quote or zero open interest (illiquid noise).
    """
    rows = []
    for inst in instruments:
        q = quotes_by_id.get(inst["id"])
        if q is None:
            continue
        oi = q.get("open_interest") or 0
        if oi <= 0:
            continue  # no open interest = no dealer exposure at this strike
        rows.append({
            "strike": float(inst["strike_price"]),
            "contract_type": inst["type"],
            "open_interest": oi,
            "implied_volatility": q.get("implied_volatility"),
            "robinhood_gamma": q.get("gamma"),
        })
    return rows


def compute_gex_profile(spot_price: float, chain: list, expiration_date: str) -> list[StrikeGEX]:
    """
    chain: output of build_chain_rows() above.
    Returns list of StrikeGEX sorted by strike ascending.

    Prefers Robinhood's own `gamma` field per contract when populated.
    Falls back to Black-Scholes off Robinhood's implied_volatility if
    gamma is null. Skips a contract entirely if neither is available -
    logged via the caller, not silently guessed at.
    """
    now = datetime.now(timezone.utc)
    expiry = datetime.strptime(expiration_date, "%Y-%m-%d").replace(
        hour=21, minute=0, tzinfo=timezone.utc  # ~4pm ET close, approx
    )
    t_years = max((expiry - now).total_seconds(), 0) / (365.0 * 24 * 3600)

    by_strike: dict[float, dict] = {}
    skipped = 0
    for row in chain:
        k = row["strike"]
        by_strike.setdefault(k, {"call_gex": 0.0, "put_gex": 0.0})

        gamma = row.get("robinhood_gamma")
        if gamma is None:
            iv = row.get("implied_volatility")
            if iv is None:
                skipped += 1
                continue
            gamma = bs_gamma(spot_price, k, t_years, float(iv))

        oi = float(row["open_interest"])
        # Standard dollar-gamma-per-1%-move formula: gamma * OI * 100 * spot^2 * 0.01
        notional_gamma = gamma * oi * 100 * spot_price * spot_price * 0.01

        if row["contract_type"] == "call":
            by_strike[k]["call_gex"] += notional_gamma    # calls = positive dealer gamma
        else:
            by_strike[k]["put_gex"] += -notional_gamma     # puts = negative dealer gamma

    if skipped:
        print(f"[gex_engine] skipped {skipped} contracts with no gamma/IV data")

    profile = [
        StrikeGEX(
            strike=k,
            call_gex=v["call_gex"],
            put_gex=v["put_gex"],
            net_gex=v["call_gex"] + v["put_gex"],
        )
        for k, v in sorted(by_strike.items())
    ]
    return profile


def find_pin_zone(profile: list[StrikeGEX]) -> StrikeGEX | None:
    """Pin zone = strike with largest absolute net GEX (biggest gravitational pull)."""
    if not profile:
        return None
    return max(profile, key=lambda s: abs(s.net_gex))


def find_zero_gamma_flip(profile: list[StrikeGEX]) -> float | None:
    """Approximate zero-gamma level: strike where cumulative net GEX crosses zero."""
    cumulative = 0.0
    prev_strike, prev_cum = None, None
    for s in profile:
        cumulative += s.net_gex
        if prev_cum is not None and (prev_cum < 0) != (cumulative < 0):
            # crossed zero between prev_strike and s.strike - linear interpolate
            frac = abs(prev_cum) / (abs(prev_cum) + abs(cumulative))
            return prev_strike + frac * (s.strike - prev_strike)
        prev_strike, prev_cum = s.strike, cumulative
    return None
