# KubChuah — Customer Setup Instructions for Cowork (Rev 7-C)
CDO Consulting LLC · Automated Trading Infrastructure

One customer = one Railway service, one set of facts, one Manifest.

Code these instructions were checked against: `app.py` (v2, EOD-trailing gate) on the `main` branch at commit `2e7a368`. If `app.py` has changed since then, re-run the Part B pre-check before you trust anything in the "Code facts" section.

---

## 0. Before anything
- Read `about-me.md`, `brand-voice.md`, and `working-performance.md` in the Context folder. If the Context folder is not there, report Blocked and ask Marwan where it is.
- Read KubChuah Business Plan Rev 7. If these instructions and Rev 7 disagree, stop and ask Marwan.
- Roles: **Customer** gives facts and secrets. **Marwan** approves. **Cowork** prepares, checks, and reports.
- Cowork never goes live, never merges code, and never changes a customer's Pine script.

## 1. Customer must already have
1. A prop firm account that trades through Tradovate.
2. A TradingView strategy that sends alerts (not a click-by-hand setup).
3. A PickMyTrade account connected to their Tradovate login.
4. A Telegram account.
5. Tradovate Sim access for the demo tests (Part D). No Sim = Blocked.

If any item is missing, report Blocked and stop.

---

## Part A — Collect the facts (Customer)
6. **Firm facts:** firm name, eval or funded, starting balance, profit target, max drawdown ($), drawdown type (intraday trail / EOD trail / static), trail lock level, daily loss limit, consistency %, max contracts, must-be-flat time, overnight allowed?, news rules, and the firm's written OK on automation (with date).
7. **Strategy facts:** strategy name, entry order IDs (exact case), exit order IDs (must be different from entry IDs), contract and symbol, point value, trading hours.
8. **PickMyTrade facts:** the exact key for contract count, the exact key for order ID, and whether an account ID is present.
9. **Gate settings** (Marwan proposes, customer signs): HAIRCUT_PCT, DAILY_CAP_AMOUNT, ENTRY_CUTOFF_ET (about 9 minutes before flat time), ENTRY_RESUME_ET, MAX_EQUITY_DRIFT, news blackout on/off and window.

**Cowork checks — any "yes" = Blocked:**
- Any field blank or "unknown". Never fill a field from another customer, an old plan, or a guess.
- Entry IDs and exit IDs overlap.
- Any entry or exit ID equals `EODAnchor` (the gate reserves that ID).
- Trail lock is missing.
- Drawdown type is not EOD trail (see Code facts — the current code handles EOD trail only).
- DAILY_CAP_AMOUNT is higher than the firm's daily loss limit (if the firm has one).
- The firm's max contracts is lower than what the gate's size ladder allows (see Code facts), unless Marwan approves in writing.
- No written OK on automation from the firm.

---

## Part B — Build the gate (Cowork prepares, Marwan approves)
10. **Pre-check.** Open `app.py` and report, for each Rev 7 required code change, Built or NOT BUILT:
    - Persistent state file (`STATE_PATH`)
    - `DD_MODE` (support for drawdown types other than EOD trail)
    - `LADDER_CONFIG` (size ladder set by variable, not hard-coded)
    - Config hash on `/health`
    - `MAX_EQUITY_DRIFT` check
    - News blackout

    As of commit `2e7a368`, all six are **NOT BUILT**. Only customers whose firm uses EOD trailing drawdown can be served.
11. Create a new Railway service for this customer. Paid plan. Start command uses exactly 1 worker: `gunicorn app:app --workers 1` (already set in `railway.json` and `Procfile` — confirm it was not changed).
12. Add a volume at `/data`. Set `LOG_PATH=/data/decisions.csv`. Also set `STATE_PATH=/data/state.json` so it is ready, but mark it "not read by code" in the Manifest until state persistence is built.
13. Set the non-secret Railway variables from this customer's Part A facts only. Use the variable table in Code facts. **Set every variable explicitly, even ones with a code default** — the defaults were written for one Legends Trading account, not for this customer.
14. **Secrets.** The customer types these directly into Railway and PickMyTrade. Cowork only confirms they are present, never their values:
    - `TELEGRAM_TOKEN`
    - `SECRET_PATH` (the webhook secret path — a long random string)
    - `PMT_WEBHOOK_URL` and the PickMyTrade token / account ID inside the customer's JSON
15. **Variable Manifest.** One page, three columns per setting: customer fact | Railway value | code/config value. Secrets show "present / missing" only.
    - Include every variable in the Code facts table, plus every Part A fact that has no variable (mark those "manual rule — not enforced by gate").
    - Customer signs it. Marwan signs it.
    - No tests and no go-live without both signatures and all three columns matching.

---

## Part C — Connect everything
16. **Telegram** (customer, or Marwan on a call): create the bot. The token goes straight into Railway as `TELEGRAM_TOKEN`. `TELEGRAM_CHAT_ID` is the number from getUpdates, not the bot's name. The customer opens the getUpdates link themselves, because that link contains the token. The customer must send the bot one message first, or getUpdates comes back empty.
17. **TradingView alert** (Cowork drafts, customer pastes):
    - Start from the customer's PickMyTrade JSON.
    - Add `"order_id": "{{strategy.order.id}}"` (or the customer's ID key — it must match `ID_FIELD`).
    - Add `"equity": {{strategy.equity}}` (no quotes).
    - Set the action field to `"{{strategy.order.action}}"`, never a fixed value.
    - Trigger = Order fills. Webhook = gate URL + `/` + secret path.
    - Show the JSON with all secrets redacted. Do not edit the Pine script.
    - Confirm the order IDs the Pine script actually sends match `ENTRY_ORDER_IDS` letter for letter. **A typo here means entries pass through the gate unchecked** (see Code facts).
18. **EOD anchor alert** (Cowork drafts, customer sets up): a second alert that sends, once per day after the flat time, the same webhook with `"order_id": "EODAnchor"` and `"equity": {{strategy.equity}}`. Without it the floor never moves and day P&L is measured from the starting balance forever.

---

## Part D — Prove it works (demo only)
19. **No gate first:** point the alert straight at PickMyTrade on the demo account. Fire one manual alert. Confirm a fill in Tradovate Sim.
20. **Through the gate:** point the alert at the gate, with `PMT_WEBHOOK_URL` on the test address (httpbin). Write the expected result before each test, then run the 14 Rev 7 tests. All must pass. Only Test 14 (consistency) may read NOT BUILT. Tests 8 and 9 matter most. The 14 tests are in Rev 7, not in this repo — if you can't find them, report Blocked.
21. **Live demo:** set `PMT_WEBHOOK_URL` to the demo PickMyTrade address. Fire manual alerts. Check each one in order: Telegram message → log row in `/data/decisions.csv` → PickMyTrade request → Tradovate fill. Run the 20-alert protocol. Pass line = 17 of 20, including one deliberate block.
22. **Restart test:** post an anchor, redeploy the service, then call `/health`. `day_anchor` and `eod_hwm` must match the values before the restart. If they reset to the starting balance, the volume or `LOG_PATH` is wrong — Blocked.

Write the results to the report. **Stop here.** Go-live needs Marwan's and the customer's yes.

---

## Part E — Go live and daily (Customer approves; Marwan watches)
23. Back up `decisions.csv` (and `state.json` once it exists).
24. Recheck: Manifest = Railway = `/health`. Until a config hash is built, compare by hand: every Railway variable against the Manifest, and `/health` `day_anchor` / `eod_hwm` against the last ANCHOR row in the log.
25. Customer points PickMyTrade at the real firm account and sets the real `PMT_WEBHOOK_URL`.
26. Post the first anchor from strategy equity.
27. Marwan watches the first three live signals by hand.
28. **Customer, every evening:**
    - Be flat by the firm's deadline. The gate never closes positions for you.
    - Compare strategy equity to the real account balance. If the gap is more than MAX_EQUITY_DRIFT, don't anchor and don't trade. Contact Marwan. (The gate does not check this — it is on you.)
    - Post the anchor with order ID `EODAnchor`.
    - Check `/health`.
29. **Customer, every morning:** confirm yesterday's anchor exists (Telegram message + ANCHOR row in the log) and write down the floor before the first alert.

---

## Code facts (app.py at `2e7a368`)

Use these when you fill the Manifest's "code/config value" column.

### Variables

| Variable | Required? | Code default if not set | What it does |
|---|---|---|---|
| `SECRET_PATH` | Required (secret) | — (crashes on start) | URL path the gate listens on |
| `PMT_WEBHOOK_URL` | Required (secret) | — | Where allowed orders are forwarded |
| `TELEGRAM_TOKEN` | Required (secret) | — | Bot token |
| `TELEGRAM_CHAT_ID` | Required | — | Chat ID number |
| `DD_AMOUNT` | Required | — | Max drawdown in dollars |
| `DAILY_CAP_AMOUNT` | Required | — | KubChuah's own daily loss cap |
| `QTY_FIELD` | Required | — | PickMyTrade key for contract count |
| `ENTRY_ORDER_IDS` | Required | — | Comma list of entry IDs, exact case |
| `START_BALANCE` | Set it | `50000` | Starting balance before first anchor |
| `HAIRCUT_PCT` | Set it | `20` | % of drawdown buffer held back |
| `ID_FIELD` | Set it | `order_id` | Payload key for order ID |
| `EOD_ANCHOR_ID` | Set it | `EODAnchor` | Order ID of the daily anchor alert |
| `ENTRY_CUTOFF_ET` | Set it | `16:50` | No new entries from this time |
| `ENTRY_RESUME_ET` | Set it | `18:00` | Entries allowed again from this time |
| `ENTRY_CUTOFF_TZ` | Set it | `America/New_York` | Time zone for cutoff/resume |
| `LOG_PATH` | Set it | `decisions.csv` (lost on redeploy) | Decision log — also the gate's memory |
| `STATE_PATH` | Not read | — | Reserved for Rev 7 state file |
| `MAX_EQUITY_DRIFT`, `DD_MODE`, `LADDER_CONFIG`, news blackout | Not read | — | NOT BUILT — manual rules only |

### How the gate behaves
- **Drawdown:** EOD trail only. Floor = highest anchor − `DD_AMOUNT`. Entries are blocked at highest anchor − `DD_AMOUNT` × (1 − `HAIRCUT_PCT`/100). There is no trail lock — the floor keeps rising with every new high anchor. If the firm locks the trail (for example at starting balance + $100), the gate's floor will be stricter than the firm's after that point. Note this in the Manifest.
- **Only anchors move the floor.** Intraday equity never moves it.
- **Size ladder is hard-coded** by today's P&L since the anchor: below $1,000 → max 5; $1,000–$1,999.99 → max 10; $2,000 and up → max 20. A bigger order is resized down, not blocked. There is no separate max-contracts cap — if the firm's max is below 20, the gate can let through more than the firm allows.
- **Daily cap:** blocks entries once today's P&L ≤ −`DAILY_CAP_AMOUNT`.
- **Entry cutoff:** blocks entries from `ENTRY_CUTOFF_ET` until `ENTRY_RESUME_ET` every day, weekends included. It never closes positions.
- **Exits:** any order ID that is not in `ENTRY_ORDER_IDS` and not `EODAnchor` is forwarded with no checks. There is no exit ID list. So an entry ID that is misspelled in Railway is treated as an exit and goes straight to the broker.
- **Fail-closed:** if an entry payload is missing equity or qty, the entry is blocked and Telegram says "fail-closed".
- **Bad JSON:** dropped, logged to the Railway console only, no Telegram message.
- **Telegram down:** the gate keeps working but sends no alert, and only the Railway console shows the error.
- **State:** rebuilt from the ANCHOR rows in `LOG_PATH` on every start. Lose the log, lose the floor.
- **`/health`:** returns `eod_hwm` and `day_anchor` only. No config hash.

---

## Hard rules the whole way
- One change at a time, then re-run the tests.
- If Railway, the Manifest, and the code don't all match: stop, don't trade.
- Never paste a customer's token, webhook URL, secret path, or account ID in chat or in a report.
- Never reuse anything from another customer's service, Manifest, or logs.
- Before every push: new variables are set in Railway first, the Manifest is updated, local tests are run, and the rollback commit is known.

## Report
Save to `claude/cowork-report-<customer>-<date>.md` after Marwan confirms the location.
- Mark each step Done / Blocked / Needs my approval.
- List every NOT BUILT item from the Part B pre-check and what manual rule covers it.
- End with a section called "Not fixed — for my decision".
