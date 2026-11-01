---
name: sales-local
description: Local SEO prospect analysis for CDO Consulting. Reads a business from handoff.json (made by local_seo_score.py from Google Maps scraper results), checks its website and Google profile gaps, compares it to real map competitors, and writes PROSPECT-ANALYSIS.md plus a short outreach sequence. Use for "/sales-local <business name or rank number>" or when the user wants to work a local business prospect from the scraped list.
---

# /sales-local — Local SEO Prospect Analysis (CDO Consulting)

Version 1.0. Replaces the B2B SaaS scoring in `/sales prospect` (funding, headcount,
tech spend) with local SEO scoring. The output file names match the ai-sales-team
skills, so `/sales outreach`, `/sales prep` and `/sales proposal` reuse the work.

## Input

`/sales-local <business name or rank number>`

1. Find `handoff.json` (current folder, then `output/`, then `../output/`).
   If you can't find it, stop and tell the user to run `scripts/local_seo_score.py` first.
2. Pick the entry by rank number (1 = first) or by business name match.
   If more than one name matches, list them and ask which one. Ask only that.

## Rules

- Use ONLY facts from handoff.json, the business website, and pages you fetch.
  Never invent review counts, rankings, traffic or revenue. If you don't know, write "unknown".
- Every finding says where it came from: `[Maps data]`, `[Website]`, `[PageSpeed]`, `[Screaming Frog]`.
- Plain, simple English. No buzzwords. The reader's first language may not be English.
- The score from handoff.json is the score. Do not re-score with BANT/MEDDIC.

## Step 1 — Prospect folder

Create `prospects/<slug>/` (slug = business name, lowercase, dashes). Write all files there.
Tell the user: to use `/sales outreach`, `/sales prep` or `/sales proposal` next,
open Claude Code in that folder.

## Step 2 — Google Business Profile gaps (from handoff.json)

Report: review count vs category median, rating, website type (none / free builder / own),
missing description, hours or photos, and the `reasons` field. Name the
`category_leaders` (real competitors from the same map search) with their review counts.
The gap between this business and the leaders is the main sales argument.

## Step 3 — Website check (skip if website_type is "none")

Fetch the homepage with WebFetch. Check and report yes/no:

| Check | Why it matters |
|---|---|
| City name (Warren / Eastpointe / Macomb County) in title tag or H1 | local ranking signal |
| Service named in title tag | relevance |
| Phone number visible and click-to-call (`tel:` link) | calls = leads |
| Name, address, phone match the Google listing | NAP consistency |
| LocalBusiness schema (JSON-LD) | rich results |
| Mobile viewport meta tag | mobile-first indexing |
| Clear call to action above the fold | conversion |
| Separate page per service | ranking for each service |

Optional speed check — PageSpeed Insights API (no key needed for low volume):
`https://www.googleapis.com/pagespeed/v5/runPagespeed?url=<site>&strategy=mobile`
Report the performance score and LCP. If the call fails, write "not checked".

If the user has a Screaming Frog export for this site (`internal_all.csv` or
`issues_overview_report.csv` in the prospect folder), read it and add the top 5 issues.

## Step 4 — Write PROSPECT-ANALYSIS.md

```
# <Business> — Local SEO Prospect Analysis
Prepared by CDO Consulting LLC · <date>

## Score: <score>/100 — Grade <grade>
SEO Need <need>/35 · Ability to Pay <pay>/30 · Reachability <reach>/20 · Win Potential <win>/15

## Snapshot
Category, city, phone, email, website, rating, reviews (category median)

## Top 3 problems costing them customers
1. <problem> [source] — what it costs them, in plain words
...

## Competitors in the same map search
| Business | Reviews | Rating |

## Pitch angle
<pitch_angle from handoff.json, turned into 2-3 sentences>

## Offer to lead with
One small, low-risk first offer (e.g. Google Business Profile cleanup, review system setup)
and the bigger service it leads to (monthly local SEO).

## Website check
<table from Step 3>
```

## Step 5 — Write OUTREACH-SEQUENCE.md

- 3 emails (Day 1, Day 4, Day 10), each under 120 words, each with ONE real finding.
- 1 short phone script (30 seconds) for when there's no email.
- 1 Google Business Profile message / contact-form version (under 60 words).
- Subject lines: plain, no clickbait.
- Every email ends with the sender's business postal address and a one-line
  opt-out ("Reply 'no thanks' and I won't email again.") — required by CAN-SPAM.
- Sign as Marwan Williams, CDO Consulting LLC, cdoconsul.com.

## Step 6 — Report back

Print in the terminal: score and grade, top 3 problems, the first email's subject line,
and the folder path. Then suggest one next command:
`/sales prep` if a call is booked, `/sales proposal` if they asked for pricing, otherwise send email 1.
