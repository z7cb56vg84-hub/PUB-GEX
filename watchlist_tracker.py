"""
Tracks every contract added to the watchlist: entry mark price, entry delta,
entry underlying spot, and timestamp. Never removes anything from Robinhood's
watchlist itself - that's a manual/app-side decision, not this bot's job.

Claude Code calls record_entry() right after add_option_to_watchlist() succeeds,
then periodically calls check_positions() with fresh quotes to log % moves and
flag TP/SL threshold crossings (informational only in watchlist-only mode -
nothing gets auto-closed or auto-removed).
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime

import config


@dataclass
class TrackedEntry:
    option_id: str
    ticker: str
    strike: float
    option_type: str       # "call" or "put"
    expiration_date: str
    entry_mark_price: float
    entry_delta: float | None
    entry_spot: float
    entry_time: str         # ISO timestamp


def _load_tracker() -> dict:
    if not os.path.exists(config.WATCHLIST_TRACKER_FILE):
        return {}
    with open(config.WATCHLIST_TRACKER_FILE) as f:
        return json.load(f)


def _save_tracker(data: dict) -> None:
    with open(config.WATCHLIST_TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_entry(option_id: str, ticker: str, strike: float, option_type: str,
                  expiration_date: str, entry_mark_price: float,
                  entry_delta: float | None, entry_spot: float) -> None:
    """Call this immediately after add_option_to_watchlist succeeds."""
    data = _load_tracker()
    data[option_id] = asdict(TrackedEntry(
        option_id=option_id, ticker=ticker, strike=strike, option_type=option_type,
        expiration_date=expiration_date, entry_mark_price=entry_mark_price,
        entry_delta=entry_delta, entry_spot=entry_spot,
        entry_time=datetime.now().isoformat(),
    ))
    _save_tracker(data)


@dataclass
class PositionCheck:
    option_id: str
    ticker: str
    strike: float
    option_type: str
    entry_mark_price: float
    current_mark_price: float
    pct_move: float
    entry_delta: float | None
    entry_spot: float
    current_spot: float | None
    implied_spot_move: float | None  # current_spot - entry_spot, if available
    hit_take_profit: bool
    hit_stop_loss: bool


def check_positions(current_quotes_by_id: dict, current_spots_by_ticker: dict | None = None) -> list[PositionCheck]:
    """
    current_quotes_by_id: {option_id: quote_dict} from get_option_quotes,
        where quote_dict has at least "mark_price".
    current_spots_by_ticker: {ticker: spot_price}, if you have it handy - purely
        for the "what move in the underlying produced this" context in the log,
        not required. Keyed by ticker since the tracker can hold entries across
        multiple tickers (SPY and SPX) at once - a single scalar spot would get
        misapplied to whichever ticker didn't provide it.

    Returns a PositionCheck per tracked entry that has a current quote.
    Entries with no matching quote (e.g. contract no longer quotable) are
    skipped, not errored - they stay in the tracker file untouched.
    """
    data = _load_tracker()
    current_spots_by_ticker = current_spots_by_ticker or {}
    results = []
    for option_id, entry in data.items():
        quote = current_quotes_by_id.get(option_id)
        if quote is None:
            continue
        current_mark = float(quote["mark_price"])
        entry_mark = entry["entry_mark_price"]
        pct_move = (current_mark - entry_mark) / entry_mark if entry_mark else 0.0
        current_spot = current_spots_by_ticker.get(entry["ticker"])
        implied_spot_move = (current_spot - entry["entry_spot"]) if current_spot else None

        results.append(PositionCheck(
            option_id=option_id, ticker=entry["ticker"], strike=entry["strike"],
            option_type=entry["option_type"], entry_mark_price=entry_mark,
            current_mark_price=current_mark, pct_move=pct_move,
            entry_delta=entry["entry_delta"], entry_spot=entry["entry_spot"],
            current_spot=current_spot, implied_spot_move=implied_spot_move,
            hit_take_profit=pct_move >= config.TAKE_PROFIT_PCT,
            hit_stop_loss=pct_move <= -config.STOP_LOSS_PCT,
        ))
    return results


def format_position_log(checks: list[PositionCheck]) -> str:
    """Human-readable block for appending to trade_log.md."""
    lines = [f"\n### Position check - {datetime.now().isoformat()}\n"]
    for c in checks:
        flag = " 🟢 TAKE PROFIT HIT" if c.hit_take_profit else (" 🔴 STOP LOSS HIT" if c.hit_stop_loss else "")
        spot_note = f", spot moved {c.implied_spot_move:+.2f}" if c.implied_spot_move is not None else ""
        lines.append(
            f"- {c.ticker} {c.strike} {c.option_type}: ${c.entry_mark_price:.2f} -> "
            f"${c.current_mark_price:.2f} ({c.pct_move:+.1%}{spot_note}, "
            f"entry delta {c.entry_delta}){flag}"
        )
    return "\n".join(lines)
