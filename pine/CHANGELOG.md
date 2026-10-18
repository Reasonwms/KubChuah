# Pine Script Change Log — CDO Consulting LLC / ATI Division

## TPO + VIDYA Short — `tpo_vidya_short_v1.1.pine`

### v1.1 — 2026-10-18 — set to confirmed 50K Apprentice rules

Confirmed rules: max 2 contracts / 20 micros, $3,000 target, $2,000 EOD trailing max loss,
no consistency rule, pass in 1 day.

| Change | Why | Expected effect | How to measure |
|---|---|---|---|
| Drawdown breach check uses **worst-case equity inside the bar** (the bar high for a short), not the bar-close equity. | With EOD trailing, the floor only moves at end of day, but a breach counts in real time. v1.0 could miss a breach that happened inside a bar and then recovered by the close. | Fewer fake "passes". The pass rate goes down a little but is closer to live. | Compare "Evals failed" for v1.0 vs v1.1 on the same chart and dates. |
| `Trailing floor stops at start balance` now **OFF** by default. | The firm has not confirmed a floor lock. OFF is the stricter case. | The backtest will not overstate your room after a +$2k day. | If the firm confirms a lock, turn it ON and compare the pass rate. |
| New input `Min trading days to pass` (default 1). | Matches "pass in 1 day". You can reuse it for firms that require 5 or 7 days. | No change at 1 day. | Average days to pass stays at 1 or more. |
| Daily loss cap tooltip now says it is **your** KubChuah limit, not a firm rule. | The 50K Apprentice has no daily loss limit. The $600 cap stays as your own risk rule. | None (wording only). | — |
| Consistency stays OFF (0). | No consistency rule. | — | — |

## TPO + VIDYA Short — `tpo_vidya_short_v1.0.pine`

### v1.0 — 2026-10-18 — first build

**Goal:** a short-only strategy to backtest for the 50K Apprentice evaluation
(max 2 minis / 20 micros, $3,000 target). It matches the KubChuah gate (`app.py`).

| Part | What it does | Why | Can you reuse it? |
|---|---|---|---|
| Two-Pole Oscillator | Price distance from its SMA, z-scored, then smoothed by two stacked EMAs. A signal is a cross down through its own copy from 4 bars back. | Two-pole smoothing gives a clean turn signal with less noise than RSI or Stoch. | Yes. `twoPole()` works on any series. |
| Sell only above min level (0.0) | Sell crosses only count in the upper half of the oscillator. | Shorts from a stretched-up spot, not shorts into weakness that is already done. | Yes, use it as an overbought filter. |
| VIDYA trend filter | Close must be below VIDYA, and VIDYA must be falling. | VIDYA speeds up in trends and slows down in chop (CMO-weighted), so it filters out weak trends better than a fixed EMA. | Yes. `vidyaCalc()` can stand alone. |
| ATR stop 1.5x, target 2R | Bracket from the signal close, re-anchored to the real fill price on the next bar. | A fixed R gives a known expectancy. Re-anchoring removes the gap between the signal close and the next-bar fill. | Yes, it's the standard bracket pattern. |
| Risk-based size | qty = floor(risk USD / (stop pts × point value)), capped at 2 NQ / 20 MNQ. Trade is skipped if qty < 1. | Every trade risks about the same dollars. Wide-stop days are skipped, not oversized. | Yes. |
| Gate ladder mirror | Size cap is 5 / 10 / 20 micros by today's P&L (<$1k / <$2k / $2k+), same as `LADDER` in `app.py`. | The live gate resizes orders. Without this, the backtest would show bigger size than live. | Only with the KubChuah gate. |
| Daily loss cap $600 | Blocks new entries for the rest of the day. | Matches `DAILY_CAP_AMOUNT`. | Yes. |
| Haircut 20% | Blocks entries once the buffer to the floor is under 20% of the drawdown. | Matches `HAIRCUT_PCT`. | Only with the KubChuah gate. |
| Eval simulator | Tracks EOD / intraday / static trailing floor, profit target and an optional consistency rule. It counts every simulated pass and fail, then restarts the next day. | One backtest gives a pass rate and the average days to pass, not just net profit. | Yes, for any prop strategy. |
| Session | Entries 09:35–15:30 ET, flatten at 16:30 ET (fills next bar), max 3 trades a day. | No overnight positions. Leaves margin before the 16:50 gate cutoff and the firm's close. | Yes. |
| Alerts | JSON with `order_id`, `equity`, qty field, `sl`, `tp`. Also sends a daily `EODAnchor` alert after flatten. | Works with the gate's entry/exit routing and its EOD anchor. | Yes. |

**Repainting / look-ahead check:** no `request.security`, no negative offsets, no `barstate.isrealtime` logic,
`calc_on_every_tick=false`, orders fill at the next bar open. Pass.

**Known limits (v1.0):**
- Intraday trailing uses bar-close equity, so it misses intrabar highs. Live is stricter than the backtest.
- The `equity` in the alerts is backtest equity, not your real account balance.
- Default commission is $0.62 per side (MNQ). For NQ, set it to the real rate.
