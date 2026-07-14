# GEX Pin-Zone Bot

## Entry logic (trend + pin alignment)

Replaces the earlier "GEX sign only" rule. Trend comes from the same 8/21 EMA
relationship that colors the Pivot EMA in Saty's Pivot Ribbon Pro - computed
fresh from raw closes in `trend.py`, not pulled from TradingView (no API path
to that). Feed it recent 1-minute closes (at least 21 bars).

    Uptrend   + pin ABOVE spot -> CALL  (ride trend into the pin)
    Downtrend + pin BELOW spot -> PUT   (ride trend into the pin)
    Downtrend + pin ABOVE spot -> NO_TRADE, wait for flip to uptrend
    Uptrend   + pin BELOW spot -> NO_TRADE, wait for flip to downtrend

Gamma regime (positive/negative net GEX at the pin) is NOT a gate right now -
it's attached to every decision as a label only. The plan is to watch how it
correlates with outcomes in watchlist-only mode before deciding how to weight
it (e.g. holding positive-gamma trades longer, since price takes longer to
build a move in a pinning regime than in a trending one).

## Running this for a week (SPY + SPX, observation mode)

Since `WATCHLIST_ONLY = True`, the trading window widens to the full regular session
for SPY (`OBSERVATION_TRADING_START/END` in `config.py`, 9:30-16:00 ET), instead of
the tight 9:30-11:30 live-trading window. SPX gets a genuinely wider window
(`TICKER_META["SPX"]["observation_start"/"observation_end"]`, effectively all day/all
night) since it's on Cboe's near-24-hour Global Trading Hours, unlike SPY which is
regular-session-only - catching pin-zone shifts outside SPY's hours is the whole
reason SPX is tracked separately. **One caveat found during initial testing**: SPXW
option quotes appeared frozen (identical `updated_at`) across several hours outside
9:30-16:00 ET in this environment - that may just be a quirk of the specific testing
window rather than how it behaves on a real trading day. The scheduled loop checks
each quote's `updated_at` and flags stale data in the log instead of trusting it, so
watch `trade_log.md` over the first few overnight cycles to confirm quotes are
actually live. Nothing risky either way since the only output is a watchlist add, and
stale-flagged signals are never watchlisted.

**Cadence**: `TICKER_META[ticker]["check_interval_minutes"]` throttles how often each
ticker is actually processed (data pulled, signal computed), independent of how often
the loop itself fires - SPY is checked every cycle (5 min) but only within its own
window; SPX is checked every 15 min, including overnight, to keep token/API cost down
while still catching overnight shifts. `guardrails.py` enforces both the window and
the interval throttle itself (returns `allowed=False` with a reason either way) - the
loop runs on a single ~24-hour cron and just calls `check_entry_allowed()` every 5
minutes for both tickers, letting guardrails self-throttle. Expect SPY to report
"outside trading window" most of the day/night, and both tickers to often report "not
due for a check yet" between their own intervals - both are normal, not errors.

**SPX note**: 0DTE SPX is quoted under the `SPXW` weekly chain symbol, not `SPX` itself
(SPX is monthly-only) - this is set in `TICKER_META` already, but Claude Code should
confirm via `get_option_chains` that `SPXW` is right before pulling instruments, since
a wrong chain symbol will just return nothing rather than erroring loudly.

Give Claude Code this instruction to run continuously:

> Every 5 minutes, essentially around the clock, for SPY and SPX: run
> `check_entry_allowed(ticker)`. If it returns a decision dict, log it and stop for
> that ticker this cycle (this covers guardrail blocks, per-ticker windows, and the
> per-ticker check-interval throttle - both are expected to fire often, just log and
> move on). If it returns None, pull the last 30 one-minute closes, call
> `get_option_instruments` (today's 0DTE expiration - use SPXW for SPX) and
> `get_option_quotes`, then pass everything into `run_with_data()`. Check each quote's
> `updated_at` - if stale/unchanged from the last cycle, log the decision but flag it
> as stale and do not watchlist it regardless of signal. Since `WATCHLIST_ONLY` is
> True, add any fresh-data BUY_CALL/BUY_PUT hit to Robinhood's options watchlist
> instead of trading (there's no separate named list - it's the single global options
> watchlist) - never place a real order regardless of what the signal says. Log every
> cycle's decision, including NO_TRADE ones, so `trade_log.md` can be reviewed for how
> the trend+pin logic
> performed before any of this touches real money.

At the end of the week, read back through `trade_log.md` - specifically how often
NO_TRADE fired for "trend and pin disagree" vs how often a flip actually followed, and
whether the watchlisted hits would have been good entries in hindsight.

## Tracking watchlisted contracts (no auto-removal)

`watchlist_tracker.py` records entry premium + delta + spot the moment something's
added to the watchlist, so % gain/loss is always calculable later even though
nothing gets auto-removed. Robinhood's watchlist only shows *current* price, not
"since you added it" - this file is where that history actually lives.

Tell Claude Code, alongside the main loop instruction: "Right after any
`add_option_to_watchlist` call, call `watchlist_tracker.record_entry(...)` with the
option's mark_price, delta, and current spot from the quote you just pulled. Every
30 minutes, call `check_positions()` with fresh quotes for everything in the tracker
and a `current_spots_by_ticker` dict (e.g. `{'SPY': ..., 'SPX': ...}`) built from
fresh spot quotes for every ticker that has tracked entries - it's keyed by ticker
since SPY and SPX positions can be tracked at the same time. Append
`format_position_log()`'s output to `trade_log.md` - flag but do not act on
TAKE_PROFIT/STOP_LOSS hits, since nothing auto-trades or auto-removes in
watchlist-only mode."

TP/SL are set in `config.py` as `TAKE_PROFIT_PCT` (+35%) / `STOP_LOSS_PCT` (-25%) -
percentage of premium, not a dollar-of-underlying target, since 0DTE delta moves too
fast for a fixed underlying-move target to stay accurate for more than a few minutes.

## Data source: Robinhood only, no Polygon/Massive needed

Turns out this was unnecessary complexity on my part - Robinhood's own MCP tools
(`get_option_instruments`, `get_option_quotes`) already return `implied_volatility`,
`gamma`, `delta`, and `open_interest` per contract. No separate paid data feed required.

**One thing confirmed**: greeks and OI populate correctly on live, liquid contracts -
verified against a real SPY 0DTE chain. Robinhood's own `gamma`/`implied_volatility`
fields are expected to be the primary path going forward; the Black-Scholes fallback
in `gex_engine.py` stays in the code as a safety net for any contract where Robinhood's
own gamma comes back null, but isn't expected to fire in normal operation.

## What's built vs. what you still need to wire up

**Built:**
- `config.py` - all risk caps, ticker allowlist, trading window (9:30-11:30 ET), kill switch
- `guardrails.py` - enforces the caps: kill switch, allowed tickers, trading window,
  daily loss cap ($150 default), max trades/day (3), max open positions (1), 15-min cooldown
- `gex_engine.py` - pulls Robinhood's own greeks/OI, falls back to Black-Scholes off
  Robinhood's IV when needed; pin-zone finder, zero-gamma flip finder
- `trend.py` - recomputes the ribbon's 8/21 EMA crossover from raw closes
- `signal.py` - trend + pin alignment logic (see above), tags gamma regime as a label
- `main.py` - split into two steps: `check_entry_allowed()` (guardrails only, no data
  needed) and `run_with_data()` (takes Robinhood instrument/quote data Claude Code already
  pulled, and returns the trade decision). Neither step calls Robinhood directly - this
  is plain Python with no network access, by design, so it can't accidentally trade.

**Not built - you still need to:**
1. The trend+pin alignment rule in `signal.py` is your specified logic, but hasn't been
   backtested yet. This week's watchlist-only run across SPY (full session) and SPX is
   exactly for finding out if it actually works before any money is on the line.
2. Confirm `SPXW` is the right 0DTE chain symbol for SPX via `get_option_chains` before
   the first real pull - `TICKER_META` assumes it but hasn't been checked live yet.

## How this runs inside Claude Code (not this chat)

This chat (claude.ai) can't run anything unattended - it only exists while we're talking.
Claude Code is different: it's a persistent local session that can loop.

The intended flow, once you have Claude Code connected to Robinhood's Trading MCP:

1. You tell Claude Code (once) the instruction from "Running this for a week" above -
   every 5 minutes during market hours, for each ticker in `ALLOWED_TICKERS`:
   run `check_entry_allowed(ticker)` (self-throttles per `TICKER_META`'s
   `check_interval_minutes`). If it returns a decision dict, log it and stop for that
   ticker this cycle. If it returns None, pull the last 30 one-minute closes
   (get_equity_historicals), call `get_option_instruments` (today's expiration, right
   chain_symbol from `TICKER_META`) and `get_option_quotes` for those instrument IDs,
   then pass everything into `run_with_data()`. If the result has `action` BUY_CALL or
   BUY_PUT and `WATCHLIST_ONLY` is True, add that contract to Robinhood's options
   watchlist (the single global list - there's no named sub-list) instead of trading.
   If `WATCHLIST_ONLY` is False, place the trade via the Robinhood MCP using
   `max_contracts`. Otherwise do nothing.
2. Claude Code has to stay open on your machine for this to actually run - it's not a
   cloud cron job. Laptop closed = bot stopped, which is a feature, not a bug.
3. After every trade Claude Code places, it should call `guardrails.record_trade(...)`
   (you'll want to ask Claude Code to do this as part of the same instruction) so the
   daily loss cap and trade count actually track reality.

## Kill switch

`touch KILL_SWITCH` in this folder any time you want to hard-stop it, no questions asked.
Delete the file to resume. This is checked first, before anything else, every single run.

## Before this touches real money

- Run it in log-only mode for at least a few full sessions (let `allowed: true` decisions
  print but tell Claude Code NOT to actually place them yet) and compare its picks against
  what you'd have done manually.
- Confirm the GEX numbers match your existing `gex_0dte.py` output on the same day/ticker.
- Start with `MAX_CONTRACTS_PER_TRADE = 1` and the $150 daily cap exactly as configured -
  don't loosen these until you've watched it run for real.
