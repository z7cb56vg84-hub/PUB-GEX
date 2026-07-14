"""
All hard risk limits live here. This module is the last gate before any
trade goes out - main.py calls check_guardrails() and only proceeds to
Claude Code / the Robinhood MCP call if it returns allowed=True.

This is deliberately dumb and rule-based, not "smart." The whole point is
that it doesn't reinterpret or get talked out of the limits.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import config


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str


def _load_state() -> dict:
    if not os.path.exists(config.STATE_FILE):
        return {"date": None, "trades_today": 0, "realized_pnl_today": 0.0,
                "open_positions": 0, "last_trade_time": None, "last_checked_by_ticker": {}}
    with open(config.STATE_FILE) as f:
        state = json.load(f)
    state.setdefault("last_checked_by_ticker", {})  # older state files predate this field
    return state


def _save_state(state: dict) -> None:
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _reset_if_new_day(state: dict, today_str: str) -> dict:
    if state.get("date") != today_str:
        state = {"date": today_str, "trades_today": 0, "realized_pnl_today": 0.0,
                  "open_positions": state.get("open_positions", 0),  # carry over open positions
                  "last_trade_time": None, "last_checked_by_ticker": {}}
    return state


def check_guardrails(ticker: str) -> GuardrailResult:
    now_et = datetime.now(ZoneInfo(config.TIMEZONE))
    today_str = now_et.strftime("%Y-%m-%d")

    # 1. Kill switch - absolute override
    if os.path.exists(config.KILL_SWITCH_FILE):
        return GuardrailResult(False, "KILL_SWITCH file present - all trading halted")

    # 2. Ticker allowlist
    if ticker not in config.ALLOWED_TICKERS:
        return GuardrailResult(False, f"{ticker} not in ALLOWED_TICKERS {config.ALLOWED_TICKERS}")

    # 3. Trading window - full session when observing, tight window when live.
    # Per-ticker override (TICKER_META) takes precedence over the global default,
    # since tickers with extended-hours support (e.g. SPXW) may warrant a wider
    # window than an equity ETF like SPY. NOTE: a wider window only helps if the
    # data source actually has live quotes in that stretch - confirm before
    # relying on it, since SPXW quotes were observed stale/frozen outside
    # 09:30-16:00 ET during initial testing of this bot.
    meta = config.TICKER_META.get(ticker, {})
    if config.WATCHLIST_ONLY:
        window_start = meta.get("observation_start", config.OBSERVATION_TRADING_START)
        window_end = meta.get("observation_end", config.OBSERVATION_TRADING_END)
    else:
        window_start = meta.get("live_start", config.LIVE_TRADING_START)
        window_end = meta.get("live_end", config.LIVE_TRADING_END)

    if not (window_start <= now_et.time() <= window_end):
        return GuardrailResult(
            False,
            f"Outside trading window ({window_start}-{window_end} ET, "
            f"{'observation' if config.WATCHLIST_ONLY else 'live'} mode). "
            f"Current time: {now_et.time().strftime('%H:%M')} ET"
        )

    state = _load_state()
    state = _reset_if_new_day(state, today_str)

    # 4. Per-ticker check-interval throttle. This is a cost control, not a risk
    # cap - it exists so a single 5-minute cron loop can poll SPY every cycle
    # while polling a less time-sensitive ticker (e.g. SPX) less often, without
    # needing a second scheduled job. Every call that clears this (whether or
    # not it passes the checks below) updates the ticker's last-checked time,
    # so the throttle window starts from the most recent *attempt*, not the
    # most recent *allowed* result.
    interval_min = meta.get("check_interval_minutes", 5)
    last_checked = state["last_checked_by_ticker"].get(ticker)
    if last_checked:
        elapsed_min = (now_et - datetime.fromisoformat(last_checked)).total_seconds() / 60
        if elapsed_min < interval_min:
            return GuardrailResult(
                False,
                f"{ticker} not due for a check yet ({elapsed_min:.1f} min since last, "
                f"need {interval_min} min)"
            )
    state["last_checked_by_ticker"][ticker] = now_et.isoformat()
    _save_state(state)

    # 5. Daily loss cap
    if state["realized_pnl_today"] <= -abs(config.MAX_DAILY_LOSS_DOLLARS):
        return GuardrailResult(
            False,
            f"Daily loss cap hit (${state['realized_pnl_today']:.2f} <= "
            f"-${config.MAX_DAILY_LOSS_DOLLARS}). No more trades today."
        )

    # 6. Max trades per day
    if state["trades_today"] >= config.MAX_TRADES_PER_DAY:
        return GuardrailResult(
            False, f"Max trades per day reached ({config.MAX_TRADES_PER_DAY})"
        )

    # 7. Max open positions
    if state["open_positions"] >= config.MAX_OPEN_POSITIONS:
        return GuardrailResult(
            False, f"Max open positions reached ({config.MAX_OPEN_POSITIONS}) - close before opening new"
        )

    # 8. Cooldown between trades
    if state["last_trade_time"]:
        last = datetime.fromisoformat(state["last_trade_time"])
        elapsed_min = (now_et - last).total_seconds() / 60
        if elapsed_min < config.MIN_MINUTES_BETWEEN_TRADES:
            return GuardrailResult(
                False,
                f"Cooldown active - {elapsed_min:.1f} min since last trade, "
                f"need {config.MIN_MINUTES_BETWEEN_TRADES} min"
            )

    _save_state(state)  # persist any date-reset
    return GuardrailResult(True, "All guardrails passed")


def record_trade(realized_pnl_delta: float = 0.0, opened_position: bool = False,
                  closed_position: bool = False) -> None:
    """Call this after Claude Code actually places/closes a trade via the MCP."""
    now_et = datetime.now(ZoneInfo(config.TIMEZONE))
    today_str = now_et.strftime("%Y-%m-%d")
    state = _load_state()
    state = _reset_if_new_day(state, today_str)

    if opened_position:
        state["trades_today"] += 1
        state["open_positions"] += 1
        state["last_trade_time"] = now_et.isoformat()
    if closed_position:
        state["open_positions"] = max(0, state["open_positions"] - 1)
    state["realized_pnl_today"] += realized_pnl_delta

    _save_state(state)
