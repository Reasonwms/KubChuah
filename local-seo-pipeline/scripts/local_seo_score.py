#!/usr/bin/env python3
"""
Local SEO Prospect Scorer  —  CDO Consulting LLC
Version 1.0

Reads the CSV that gosom/google-maps-scraper produces and scores each
business as a local SEO prospect (0-100). Replaces the B2B SaaS scoring
in ai-sales-team-claude (funding, headcount, tech spend), which does not
fit local service businesses.

Score = SEO Need (35) + Ability to Pay (30) + Reachability (20) + Win Potential (15)

Outputs (in --out folder):
  scored.csv        every business, best first, with score, grade, pitch angle
  top-prospects.md  short report of the top N
  handoff.json      top N in a format the /sales-local skill reads
  sf-urls.txt       websites of A/B prospects for Screaming Frog list mode

Usage:
  python3 local_seo_score.py results.csv --out output --top 25
  python3 local_seo_score.py results.csv --cities "Warren,Eastpointe"

Only uses the Python standard library.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

csv.field_size_limit(sys.maxsize)

# ---------------------------------------------------------------------------
# Category value tiers (how much a new customer is worth -> SEO budget)
# Matched as lowercase substrings of the Google category.
# ---------------------------------------------------------------------------
HIGH_VALUE = [
    "roof", "plumb", "hvac", "heating", "air conditioning", "furnace",
    "electrician", "electrical", "general contractor", "remodel", "kitchen",
    "bathroom", "basement", "foundation", "concrete", "paving", "asphalt",
    "garage door", "pest", "tree service", "water damage", "restoration",
    "mold", "window", "siding", "gutter", "fence", "deck",
    "dentist", "dental", "orthodont", "oral surg", "chiropract",
    "medical spa", "med spa", "plastic surg", "physical therap",
    "attorney", "lawyer", "law firm", "auto body", "collision",
]
MID_VALUE = [
    "auto repair", "mechanic", "car repair", "tire", "transmission",
    "landscap", "lawn", "snow", "cleaning", "carpet", "moving", "mover",
    "insurance", "real estate", "realtor", "veterinar", "animal hospital",
    "accountant", "tax", "daycare", "child care", "locksmith", "towing",
    "optometr", "eye care", "massage", "physiother", "counsel", "therap",
]
# anything else = low value (restaurants, salons, retail, etc.)

# Website hosts that signal "no real website" (free builders, social pages)
WEAK_SITE_HOSTS = [
    "facebook.com", "instagram.com", "business.site", "godaddysites.com",
    "wixsite.com", "weebly.com", "square.site", "yelp.com", "linktr.ee",
    "sites.google.com", "carrd.co", "thumbtack.com", "nextdoor.com",
]

# Chains / franchises: corporate controls marketing, rarely buy local SEO.
CHAIN_WORDS = [
    "jiffy lube", "valvoline", "midas", "meineke", "firestone", "pep boys",
    "goodyear", "aspen dental", "heartland dental", "terminix", "orkin",
    "roto-rooter", "mr. rooter", "servpro", "servicemaster", "stanley steemer",
    "molly maid", "merry maids", "re/max", "keller williams", "coldwell banker",
    "century 21", "state farm", "allstate", "farmers insurance", "progressive",
    "banfield", "vca ", "great clips", "supercuts", "home depot", "lowe's",
]


MIN_PEERS = 5        # businesses needed in a category before comparing reviews
NO_GAP_CAP = 55      # max score when the business has almost no SEO gaps


def to_int(v):
    try:
        return int(float(str(v).strip() or 0))
    except ValueError:
        return 0


def to_float(v):
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def parse_json(v, default):
    v = (v or "").strip()
    if not v:
        return default
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def category_tier(cat):
    c = (cat or "").lower()
    if any(k in c for k in HIGH_VALUE):
        return "high"
    if any(k in c for k in MID_VALUE):
        return "mid"
    return "low"


def website_quality(url):
    """Return 'none', 'weak' (free builder / social page) or 'own'."""
    u = (url or "").strip().lower()
    if not u:
        return "none"
    if any(h in u for h in WEAK_SITE_HOSTS):
        return "weak"
    return "own"


def city_of(row):
    addr = parse_json(row.get("complete_address"), {})
    if isinstance(addr, dict) and addr.get("city"):
        return addr["city"].strip()
    m = re.search(r",\s*([A-Za-z .'-]+),\s*MI\b", row.get("address", ""))
    return m.group(1).strip() if m else ""


def has_hours(row):
    h = parse_json(row.get("open_hours"), {})
    return bool(h)


def image_count(row):
    imgs = parse_json(row.get("images"), [])
    return len(imgs) if isinstance(imgs, list) else 0


def owner_listed(row):
    """Owner info is present in the listing data. Weak signal that the
    profile is claimed. Verify by hand before you pitch 'claim your profile'."""
    o = parse_json(row.get("owner"), {})
    return bool(isinstance(o, dict) and (o.get("id") or o.get("name")))


def closed_state(row):
    """'gone' = permanently/temporarily closed (disqualify).
    'verify' = bare CLOSED enum from Google; meaning not certain, check by hand.
    '' = fine. Note: 'Closed ⋅ Opens 9 AM' only means closed at scrape time."""
    s = (row.get("status") or "").strip().lower()
    if "permanently closed" in s or "temporarily closed" in s:
        return "gone"
    if s == "closed":
        return "verify"
    return ""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_row(row, cat_median, title_counts):
    reasons = []      # why the score is what it is
    pitch = []        # what to sell them
    reviews = to_int(row.get("review_count"))
    rating = to_float(row.get("review_rating"))
    cat = row.get("category", "")
    tier = category_tier(cat)
    site = website_quality(row.get("website"))
    median = cat_median.get(cat.lower(), 0)
    emails = [e for e in (row.get("emails") or "").split(",") if e.strip()]
    phone = (row.get("phone") or "").strip()
    title = (row.get("title") or "").strip()

    # 1. SEO Need (max 35) -------------------------------------------------
    need = 0
    if site == "none":
        need += 12
        reasons.append("no website")
        pitch.append("website + Google Business Profile setup")
    elif site == "weak":
        need += 8
        reasons.append("only a free-builder or social page as website")
        pitch.append("real website that ranks")

    if reviews < 10:
        need += 10
        reasons.append(f"only {reviews} reviews")
        pitch.append("review generation system")
    elif median and reviews < median:
        need += 6
        reasons.append(f"{reviews} reviews vs {median:.0f} median for '{cat}'")
        pitch.append("review generation to catch competitors")

    if 0 < rating < 4.0:
        need += 4
        reasons.append(f"rating {rating:.1f}")
        pitch.append("reputation management")

    gaps = []
    if not (row.get("descriptions") or "").strip():
        gaps.append("description")
    if not has_hours(row):
        gaps.append("hours")
    if image_count(row) < 3:
        gaps.append("photos")
    if gaps:
        need += min(3 * len(gaps), 9)
        reasons.append("profile missing " + ", ".join(gaps))
        pitch.append("Google Business Profile optimization")
    need = min(need, 35)

    # 2. Ability to Pay (max 30) -------------------------------------------
    pay = {"high": 18, "mid": 11, "low": 5}[tier]
    if reviews >= 20:
        pay += 6          # established, has steady customers
    elif reviews >= 5:
        pay += 3
    if site == "own":
        pay += 4          # already spends money on marketing
    if owner_listed(row):
        pay += 2
    pay = min(pay, 30)

    # 3. Reachability (max 20) ---------------------------------------------
    reach = 0
    if emails:
        reach += 10
    if phone:
        reach += 6
    if site == "own":
        reach += 4        # contact form, can research the owner
    reach = min(reach, 20)

    # 4. Win Potential (max 15) --------------------------------------------
    win = 0
    lowered = title.lower()
    chain = any(w in lowered for w in CHAIN_WORDS) or title_counts[lowered] >= 3
    if chain:
        reasons.append("chain/franchise (corporate marketing)")
    else:
        win += 7
    if rating >= 4.0 and (reviews < 10 or (median and reviews < median)):
        win += 8          # good business that customers can't find
        reasons.append("good rating but low visibility")
    elif rating >= 4.0:
        win += 4
    win = min(win, 15)

    total = need + pay + reach + win

    # Already strong: little to fix, weak cold-outreach target --------------
    if need < 5:
        total = min(total, NO_GAP_CAP)
        reasons.append("already strong online - retainer/maintenance pitch only")
        pitch = ["ongoing SEO retainer to protect rankings"]

    # Disqualifiers --------------------------------------------------------
    flag = ""
    state = closed_state(row)
    if state == "gone":
        total, flag = 0, "closed"
    elif not emails and not phone and site == "none":
        total, flag = min(total, 20), "no way to contact"
    elif state == "verify":
        reasons.append("Google shows CLOSED - confirm still in business")

    return {
        "score": total,
        "grade": grade(total),
        "need": need, "pay": pay, "reach": reach, "win": win,
        "tier": tier,
        "website_type": site,
        "category_median_reviews": round(median, 1),
        "flag": flag,
        "reasons": "; ".join(reasons),
        "pitch_angle": ", ".join(dict.fromkeys(pitch)) or "local SEO growth",
        "emails_found": ", ".join(e.strip() for e in emails),
    }


def grade(score):
    # Same bands as ai-sales-team-claude so both tools read the same
    if score >= 90:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # dedupe across queries (same business found by two searches)
    seen, out = set(), []
    for r in rows:
        key = r.get("place_id") or r.get("cid") or (r.get("title", "") + r.get("address", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out, len(rows) - len(out)


def main():
    ap = argparse.ArgumentParser(description="Score gosom Google Maps results as local SEO prospects.")
    ap.add_argument("input_csv", help="CSV produced by google-maps-scraper")
    ap.add_argument("--out", default="output", help="output folder (default: output)")
    ap.add_argument("--top", type=int, default=25, help="how many prospects in the report (default 25)")
    ap.add_argument("--cities", default="", help="keep only these cities, comma separated, e.g. 'Warren,Eastpointe'")
    args = ap.parse_args()

    rows, dupes = load_rows(args.input_csv)
    if args.cities:
        keep = {c.strip().lower() for c in args.cities.split(",") if c.strip()}
        rows = [r for r in rows if city_of(r).lower() in keep]

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[(r.get("category") or "").lower()].append(to_int(r.get("review_count")))
    # median only means something with enough peers in the category
    cat_median = {c: statistics.median(v) for c, v in by_cat.items() if len(v) >= MIN_PEERS}
    title_counts = Counter((r.get("title") or "").strip().lower() for r in rows)

    scored = []
    for r in rows:
        s = score_row(r, cat_median, title_counts)
        scored.append({
            "score": s["score"], "grade": s["grade"],
            "title": r.get("title", ""), "category": r.get("category", ""),
            "city": city_of(r), "phone": r.get("phone", ""),
            "emails": s["emails_found"], "website": r.get("website", ""),
            "review_count": to_int(r.get("review_count")),
            "review_rating": round(to_float(r.get("review_rating")), 1),
            "category_median_reviews": s["category_median_reviews"],
            "pitch_angle": s["pitch_angle"], "reasons": s["reasons"],
            "need": s["need"], "pay": s["pay"], "reach": s["reach"], "win": s["win"],
            "value_tier": s["tier"], "website_type": s["website_type"],
            "flag": s["flag"], "maps_link": r.get("link", ""),
            "place_id": r.get("place_id", ""), "input_id": r.get("input_id", ""),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)

    os.makedirs(args.out, exist_ok=True)
    if scored:
        with open(os.path.join(args.out, "scored.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(scored[0].keys()))
            w.writeheader()
            w.writerows(scored)

    top = [s for s in scored if not s["flag"]][: args.top]

    # competitors = top 3 by reviews in the same category (map-pack benchmark)
    leaders = defaultdict(list)
    for s in sorted(scored, key=lambda x: x["review_count"], reverse=True):
        if len(leaders[s["category"]]) < 3:
            leaders[s["category"]].append({"name": s["title"], "reviews": s["review_count"], "rating": s["review_rating"]})

    handoff = []
    for s in top:
        item = dict(s)
        item["category_leaders"] = [l for l in leaders[s["category"]] if l["name"] != s["title"]]
        handoff.append(item)
    with open(os.path.join(args.out, "handoff.json"), "w", encoding="utf-8") as f:
        json.dump(handoff, f, indent=2)

    with open(os.path.join(args.out, "sf-urls.txt"), "w", encoding="utf-8") as f:
        for s in scored:
            if s["grade"] in ("A+", "A", "B") and s["website_type"] == "own" and not s["flag"]:
                f.write(s["website"].strip() + "\n")

    grades = Counter(s["grade"] for s in scored)
    lines = [
        "# Local SEO Prospects — CDO Consulting",
        "",
        f"Businesses scored: {len(scored)} (duplicates removed: {dupes})",
        "Grades: " + ", ".join(f"{g}: {grades.get(g, 0)}" for g in ["A+", "A", "B", "C", "D"]),
        "",
        "Score = SEO Need (35) + Ability to Pay (30) + Reachability (20) + Win Potential (15)",
        "",
        "| # | Score | Business | Category | City | Reviews (median) | Rating | Email | Pitch |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, s in enumerate(top, 1):
        lines.append(
            f"| {i} | {s['score']} {s['grade']} | {s['title']} | {s['category']} | {s['city']} | "
            f"{s['review_count']} ({s['category_median_reviews']:.0f}) | {s['review_rating']} | "
            f"{'yes' if s['emails'] else 'no'} | {s['pitch_angle']} |"
        )
    with open(os.path.join(args.out, "top-prospects.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Scored {len(scored)} businesses ({dupes} duplicates removed).")
    print("Grades: " + ", ".join(f"{g}={grades.get(g, 0)}" for g in ["A+", "A", "B", "C", "D"]))
    print(f"Wrote: {args.out}/scored.csv, top-prospects.md, handoff.json, sf-urls.txt")


if __name__ == "__main__":
    main()
