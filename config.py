"""
Config for the GEX / pin-zone automation bot.
Edit these values directly. Nothing here calls Robinhood - this is pure
risk/signal configuration that main.py and guardrails.py read from.
"""

from datetime import time

# ---- Mode ----
# True: bot only adds signal hits to Robinhood's options watchlist to review.
# False: bot actually places trades (still gated by every cap below).
# Start with True. Don't flip to False until you've watched it flag real
# setups accurately for at least a few sessions.
WATCHLIST_ONLY = True
# NOTE: there is no such thing as a named/custom options watchlist in the
# Robinhood MCP tools - add_option_to_watchlist and get_option_watchlist both
# operate on a single global options watchlist for the account. An earlier
# version of this bot had a WATCHLIST_NAME config value implying otherwise;
# that was never real and has been removed. Anything this bot watchlists
# lands in the same list as anything you've added manually from the app.

# ---- Universe ----
# Only these tickers will ever be considered. Keep this tight.
ALLOWED_TICKERS = ["SPY", "SPX"]

# Per-ticker metadata: underlying_type Robinhood needs, strike spacing for
# the OTM offset math in signal.py, the noise-filter threshold, and polling
# cadence / trading-window overrides for guardrails.py.
#
# check_interval_minutes: guardrails.py throttles how often a ticker is
# actually processed. This is now backed by two SEPARATE scheduled tasks
# rather than one task doing both tickers at one shared cadence - a single
# shared-cadence loop meant either SPY was starved to 15 min, or SPX (and
# the outer invocation cost) ran needlessly often overnight just to serve
# SPY's finer need during the day. Two tasks fix that cleanly:
#   - "gex-bot-spy-market-hours": SPY only, every 5 min, market hours only.
#     Never fires outside SPY's window at all - not even to be throttled
#     away, since the cron itself doesn't invoke it then.
#   - "gex-bot-watchlist-observation": SPX only, every 15 min, around the
#     clock (Cboe's near-24-hour Global Trading Hours - the whole reason
#     SPX is tracked separately from SPY in the first place).
# check_interval_minutes here just documents each ticker's intended cadence
# for the corresponding task's own guardrail throttle - it's not meant to
# be read as "both tickers share one loop" anymore.
#
# strike_window: how far above/below spot (in the ticker's own price units)
# to pull instruments/quotes for. Kept tight deliberately - an earlier run
# pulled spot +/- 150 for SPX (120 contracts, ~16 strikes both sides at the
# $5 step) purely from a vague prompt instruction, which is unnecessarily
# expensive for a 0DTE pin search that's realistically going to land within
# a much smaller band of spot. +/-20 for SPY and +/-40 for SPX are both
# generous enough to still catch the pin zone without pulling the whole
# visible chain.
#
# observation_start/end, live_start/end: per-ticker override for
# guardrails.py's trading-window check; falls back to the global
# OBSERVATION_/LIVE_TRADING_START/END below when omitted.
#
# SPX gets a genuine overnight window (00:00-23:59:59, effectively no
# window restriction) - it's on Cboe's near-24-hour Global Trading Hours,
# unlike SPY which is regular-session-only. This is the whole point of
# tracking SPX separately from SPY: catching pin-zone shifts overnight.
# NOTE: during initial testing in this environment, SPXW quotes appeared
# frozen (unchanged updated_at) outside 09:30-16:00 ET - that may just be a
# quirk of this specific data feed/testing window rather than how it
# behaves on a real trading day. Check trade_log.md's logged updated_at
# timestamps on real overnight runs to confirm quotes are actually live;
# if they're consistently stale, that's worth revisiting.
#
# SPX 0DTE is quoted under the "SPXW" weekly chain symbol, not "SPX" (SPX
# itself is monthly-only) - confirmed via get_option_chains.
TICKER_META = {
    "SPY": {"underlying_type": "equity", "strike_step": 1.0, "chain_symbol": "SPY",
            "min_pin_gex_threshold": 100_000_000,
            "check_interval_minutes": 5,
            "strike_window": 20},
    "SPX": {"underlying_type": "index",  "strike_step": 5.0, "chain_symbol": "SPXW",
            "min_pin_gex_threshold": 1_000_000_000,
            "check_interval_minutes": 15,
            "strike_window": 40,
            "observation_start": time(0, 0), "observation_end": time(23, 59, 59)},
}

# ---- Trading window (ET) ----
# LIVE_TRADING_WINDOW: the tested, tight window - first 2 hours only.
# Used whenever WATCHLIST_ONLY is False (real money moving), for any ticker
# that doesn't set its own live_start/live_end in TICKER_META.
LIVE_TRADING_START = time(9, 30)
LIVE_TRADING_END   = time(11, 30)

# OBSERVATION_WINDOW: full regular session. Used whenever WATCHLIST_ONLY is
# True, since nothing but a watchlist add happens - safe to observe the
# signal across the whole day to see how the trend+pin logic holds up
# outside the first two hours, before ever trusting it with real orders
# in the untested part of the session. Applies to any ticker that doesn't
# set its own observation_start/observation_end in TICKER_META.
OBSERVATION_TRADING_START = time(9, 30)
OBSERVATION_TRADING_END   = time(16, 0)

TIMEZONE = "America/New_York"

# ---- Risk caps (hard limits, not suggestions) ----
MAX_CONTRACTS_PER_TRADE = 1        # single contract per entry, no scaling in
MAX_OPEN_POSITIONS = 1              # don't stack multiple 0DTE positions at once
MAX_DAILY_LOSS_DOLLARS = 150.0       # kill switch trips once realized+open loss hits this
MAX_TRADES_PER_DAY = 3               # hard cap regardless of signal count
MIN_MINUTES_BETWEEN_TRADES = 15      # cooldown, prevents rapid-fire re-entries

# ---- Entry logic ----
# "1 OTM from pin zone" - offset in strikes, not dollars
OTM_STRIKE_OFFSET = 1

# NOTE: MIN_PIN_GEX_THRESHOLD moved into TICKER_META (per-ticker) - SPX's
# per-strike GEX runs ~100x SPY's magnitude (spot^2 scaling on a $7,500+
# index vs a ~$750 ETF), so one shared global threshold made the noise
# filter meaningless for SPX (nearly every strike cleared it). See
# TICKER_META above for each ticker's threshold.

# ---- Take profit / stop loss (percentage of premium, not dollar-of-underlying) ----
# 0DTE gamma makes delta move too fast for a fixed "$1 SPY move" target to stay
# accurate for more than a few minutes - percentage-of-premium is the stable
# reference. These are starting points, not backtested - watch how they line
# up against your actual watchlisted moves before trusting them.
TAKE_PROFIT_PCT = 0.35   # +35% on premium
STOP_LOSS_PCT = 0.25     # -25% on premium

# ---- State files ----
STATE_FILE = "bot_state.json"     # tracks trades placed today, daily P&L, kill-switch status
LOG_FILE = "trade_log.md"         # human-readable log of every decision, trade or no-trade
WATCHLIST_TRACKER_FILE = "watchlist_tracker.json"  # entry price/delta snapshot per watchlisted contract

# ---- Kill switch ----
# If this file exists, the bot refuses to place any trades, period.
# Create it manually (`touch KILL_SWITCH`) any time you want a hard stop.
KILL_SWITCH_FILE = "KILL_SWITCH"
