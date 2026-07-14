"""
Entry point. Claude Code runs this on a loop/interval (e.g. every 5 min
during the trading window). It does NOT place trades itself - it only
computes a decision and prints structured JSON. Claude Code (the agent,
connected to Robinhood's Trading MCP) reads that JSON and is the one that
actually calls place_option_order - only if allowed=True.

This split matters: the Python script owns the deterministic risk math
(guardrails, GEX calc). The agent owns the actual MCP call. Neither one
should duplicate the other's job.

Usage (from Claude Code):
    python main.py SPY
    python main.py QQQ
"""

"""
NOTE ON DATA SOURCE: this pulls the 0DTE options chain and quotes directly
from Robinhood (get_option_instruments + get_option_quotes) - no Polygon/
Massive subscription needed. Since this script itself can't call MCP tools
(it's plain Python, no network path to Robinhood), the actual tool calls
happen in Claude Code, which passes the raw results in here. See the
run_with_data() function below - that's the one Claude Code actually
calls, feeding it real instrument + quote data it just pulled.
"""

import sys
import json
from datetime import datetime, date

import config
import guardrails
from gex_engine import compute_gex_profile, build_chain_rows
from signal import build_signal
from trend import classify_trend


def log_decision(ticker: str, decision: dict) -> None:
    with open(config.LOG_FILE, "a") as f:
        f.write(f"\n### {datetime.now().isoformat()} - {ticker}\n")
        f.write(f"```json\n{json.dumps(decision, indent=2, default=str)}\n```\n")


def check_entry_allowed(ticker: str) -> dict | None:
    """
    Step 1 - call this first. If it returns a dict, guardrails blocked the
    trade and Claude Code should stop here (don't bother pulling data).
    Returns None if guardrails pass, meaning proceed to pull chain data.
    """
    gr = guardrails.check_guardrails(ticker)
    if not gr.allowed:
        decision = {"allowed": False, "reason": gr.reason, "ticker": ticker}
        log_decision(ticker, decision)
        return decision
    return None


def run_with_data(ticker: str, spot_price: float, instruments: list, quotes_by_id: dict,
                   expiration_date: str, recent_closes: list) -> dict:
    """
    Step 2 - Claude Code calls this after pulling real data from Robinhood:
        instruments = get_option_instruments(chain_symbol=ticker, expiration_dates=today)
        quotes_by_id = {id: quote, ...} built from get_option_quotes(instrument_ids=[...])
        recent_closes = closing prices from get_equity_historicals, oldest first,
            at least 21 bars (matches the ribbon's slow EMA length) - 1-minute bars
            recommended to match intraday pin-zone timing.

    Only call this if check_entry_allowed() returned None (guardrails passed).
    """
    chain = build_chain_rows(instruments, quotes_by_id)
    profile = compute_gex_profile(spot_price, chain, expiration_date)
    trend = classify_trend(recent_closes)
    signal = build_signal(ticker, spot_price, profile, trend)

    decision = {
        "allowed": True,
        "ticker": ticker,
        "action": signal.action,
        "strike": signal.strike,
        "max_contracts": config.MAX_CONTRACTS_PER_TRADE,
        "reason": signal.reason,
        "pin_strike": signal.pin_strike,
        "net_gex_at_pin": signal.net_gex_at_pin,
        "zero_gamma_level": signal.zero_gamma_level,
        "trend_direction": signal.trend_direction,
        "gamma_regime": signal.gamma_regime,
        "contracts_used_in_calc": len(chain),
    }
    log_decision(ticker, decision)
    return decision


if __name__ == "__main__":
    # Standalone CLI mode only checks guardrails - it can't pull Robinhood
    # data itself (no MCP access from plain Python). Use run_with_data()
    # from within Claude Code for the full signal.
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    blocked = check_entry_allowed(ticker)
    if blocked:
        print(json.dumps(blocked, indent=2, default=str))
    else:
        print(json.dumps({
            "allowed": True,
            "ticker": ticker,
            "next_step": "Guardrails passed. Claude Code should now pull "
                          "get_option_instruments + get_option_quotes for "
                          f"{ticker} and call run_with_data() with the results."
        }, indent=2))
