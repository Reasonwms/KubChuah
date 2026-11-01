# Local SEO Lead Pipeline — CDO Consulting LLC

Version 1.0 · Target: local service businesses in Warren and Eastpointe, MI

Three tools, each doing the one job it does best:

| Step | Tool | Job | Output |
|---|---|---|---|
| 1. Find | gosom/google-maps-scraper | Pull every business from Google Maps | `results-*.csv` |
| 2. Rank | `scripts/local_seo_score.py` (this folder) | Score each one as a local SEO prospect | `scored.csv`, `top-prospects.md`, `handoff.json`, `sf-urls.txt` |
| 3. Audit | Screaming Frog (list mode) | Technical issues on the top websites | `internal_all.csv` per prospect |
| 4. Pitch | ai-sales-team-claude + `/sales-local` skill | Analysis, outreach, call prep, proposal | `prospects/<name>/*.md` |

```
Google Maps ──► gosom CSV ──► local_seo_score.py ──► top 25 (A/B grades)
                                   │                      │
                                   │ sf-urls.txt          │ handoff.json
                                   ▼                      ▼
                           Screaming Frog ──────► /sales-local <rank>
                                                         │ PROSPECT-ANALYSIS.md
                                                         ▼
                                     /sales outreach · /sales prep · /sales proposal
```

## Why this order works

- **gosom finds leads, the sales skills don't.** ai-sales-team needs a website URL to start;
  it can't discover local businesses. gosom gives you every business with phone, email,
  reviews, rating and competitors from the same search.
- **Score before you research.** The sales skills cost Claude time and tokens per prospect.
  The scorer ranks hundreds of businesses in under a second, so you only research the top 25.
- **Real competitors, not guesses.** ai-sales-team's competitor agent guesses from the web.
  Here the competitors come from the same Google Maps search (`category_leaders`), with
  real review counts. "You have 9 reviews; the top 3 roofers near you have 310, 140 and 96"
  is the strongest local SEO argument you can make.
- **Screaming Frog only on the shortlist.** The free version crawls 500 URLs. In list mode
  with homepages only, that covers your whole A/B list in one run.

## Setup (one time, on your computer)

1. Install Docker Desktop and Python 3.
2. Install the sales skills:
   ```bash
   git clone https://github.com/zubair-trabzada/ai-sales-team-claude.git
   ```
   ```bash
   cd ai-sales-team-claude && ./install.sh
   ```
3. Add the local skill:
   ```bash
   mkdir -p ~/.claude/skills/sales-local && cp skills/sales-local/SKILL.md ~/.claude/skills/sales-local/
   ```
4. Screaming Frog SEO Spider (free up to 500 URLs): screamingfrog.co.uk

## Weekly run

1. `./run.sh city` — 48 searches (24 categories × 2 cities). Start here.
   Later, `./run.sh grid` for fuller coverage of the 8 highest-value categories.
2. Open `output/scored-<date>/top-prospects.md`.
3. Screaming Frog → Mode → List → upload `sf-urls.txt` → export to each prospect folder.
4. In Claude Code: `/sales-local 1`, `/sales-local 2`, ... → review the drafts → send.
5. When someone replies: open Claude Code in `prospects/<name>/`, run `/sales prep`
   before the call and `/sales proposal` after it.

## The score (0–100)

| Part | Max | What earns points |
|---|---|---|
| SEO Need | 35 | No website (12) or only a free/social page (8); under 10 reviews (10) or below category median (6); rating under 4.0 (4); missing description / hours / photos (3 each) |
| Ability to Pay | 30 | High-value category 18 (roofing, HVAC, dental, legal...), mid 11, low 5; 20+ reviews (6); own website (4); owner listed (2) |
| Reachability | 20 | Email found (10), phone (6), own website (4) |
| Win Potential | 15 | Not a chain (7); good rating but low visibility (8) |

Rules: permanently/temporarily closed = 0. No phone, email or website = max 20.
Almost no gaps (need under 5) = max 55, retainer pitch only. Review median is only used
when a category has 5+ businesses. Grades match ai-sales-team: A+ 90+, A 75+, B 60+, C 40+, D below 40.

To change weights, edit the numbers in `score_row()` and the constants at the top of
the script. Log each change and why in the Change log below.

## How to measure it

Track per batch of 50 contacts: emails sent, replies, calls booked, proposals, clients won.
After 2–3 batches, compare reply rate by grade. If B-grade replies as often as A-grade,
the weights need adjusting — that's the test of whether the score works.

## Rules to follow

- CAN-SPAM: every cold email needs your real postal address and a working opt-out.
- Scraping Google Maps breaks Google's Terms of Service. Keep volumes reasonable;
  use proxies for big runs.
- The "owner listed" field is a weak sign the profile is claimed. Check by hand before
  you pitch "claim your profile".

## Change log

| Version | Date | Change | Why |
|---|---|---|---|
| 1.0 | 2026-11-01 | First version | Replace SaaS scoring (funding, headcount) that grades local businesses C/D |
