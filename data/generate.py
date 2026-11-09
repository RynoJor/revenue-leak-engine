"""Synthetic CRM data for the Revenue Leak Engine.

Generates leads, deals, a deal stage log, email replies and reps for a fictional
B2B SaaS company that sells to small businesses, with revenue leaks planted at
known, exact counts. Everything is fake: names come from Faker, company names
are built from word lists, and amounts are USD.

Deterministic: seed 42 and a fixed snapshot date, so the same Faker version
(pinned in requirements.txt) always produces byte-identical CSVs.

    python data/generate.py                # writes data/sample/ and refreshes docs/SCHEMA.md
    python data/generate.py --out data/out # writes elsewhere, leaves SCHEMA.md alone

The answer key (data/sample/answer_key/) is computed from the finished data with
the same leak definitions documented in docs/SCHEMA.md, and checked against the
records that were planted, so it can never drift from the data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# --------------------------------------------------------------------------- config

SEED = 42
AS_OF = date(2026, 10, 31)          # snapshot date: "today" for every age calculation
START = date(2026, 1, 5)            # first lead date

N_ORIGINAL_LEADS = 1910
N_EXACT_DUPES = 40
N_FUZZY_DUPES = 50
N_DUPE_DEALS = 12                   # fuzzy/exact dupes that also got their own open deal
N_DEALS_BASE = 588                  # + N_DUPE_DEALS = 600 deals
N_REPLIES = 1500

N_STALLED = 40                      # proposal deals with no activity for 30+ days
N_SLOW = 260                        # inbound leads first answered after 60+ minutes
N_NEVER = 75                        # inbound leads never followed up
MISSING = {"email": 30, "industry": 25, "company_size": 25, "region": 20}

STALL_DAYS = 30
SLOW_MINUTES = 60
NEVER_MIN_AGE_DAYS = 7
WEAK_REP = "R02"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "sample"
SCHEMA_MD = ROOT / "docs" / "SCHEMA.md"

REPS = [  # rep_id, region; names come from Faker
    ("R01", "North America"),
    ("R02", "North America"),
    ("R03", "EMEA"),
    ("R04", "EMEA"),
    ("R05", "APAC"),
    ("R06", "LATAM"),
]
REGIONS = {"North America": 0.45, "EMEA": 0.30, "APAC": 0.15, "LATAM": 0.10}

INDUSTRIES = {
    "Retail": ["Goods", "Market", "Outfitters", "Supply", "Boutique"],
    "Food & Beverage": ["Bakery", "Kitchen", "Coffee", "Bistro", "Catering"],
    "Professional Services": ["Accounting", "Legal", "Consulting", "Advisors", "Design"],
    "Health & Wellness": ["Dental", "Physio", "Wellness", "Clinic", "Pharmacy"],
    "Fitness": ["Fitness", "Yoga", "Athletics", "Gym"],
    "Beauty & Personal Care": ["Salon", "Spa", "Barbers"],
    "Real Estate": ["Realty", "Properties", "Homes"],
    "Education": ["Learning", "Tutoring", "Academy"],
    "Automotive": ["Auto", "Motors", "Tires"],
    "Hospitality": ["Inn", "Lodge", "Guesthouse"],
}
INDUSTRY_WEIGHTS = [0.16, 0.14, 0.15, 0.12, 0.08, 0.08, 0.08, 0.06, 0.07, 0.06]

PREFIXES = ["Amber", "Aspen", "Birch", "Blue", "Bright", "Cedar", "Clear", "Copper",
            "Coral", "Crest", "Elm", "Fern", "Fox", "Golden", "Granite", "Harbor",
            "Hazel", "Iron", "Juniper", "Lark", "Linden", "Maple", "Meadow", "Mill",
            "North", "Oak", "Orchid", "Pine", "Quill", "Red", "Ridge", "River", "Sage",
            "Silver", "Slate", "Stone", "Summit", "Sun", "Thistle", "Willow", "Wren",
            "Bay", "Brook", "Canyon", "Cobalt", "Dune", "Ember", "Flint", "Glen", "Hollow"]
SUFFIXES = ["field", "leaf", "wood", "stone", "brook", "gate", "haven", "crest", "side",
            "view", "point", "line", "well", "worth", "ford", "light", "bridge", "dale",
            "wick", "spring", "rock", "vale", "wind", "more", "ton"]
LEGAL = ["", " LLC", " Co.", " Inc."]
LEGAL_W = [0.55, 0.20, 0.15, 0.10]

SIZES = {"1-10": (0.50, 4800), "11-50": (0.35, 9600), "51-200": (0.15, 19200)}

SOURCES = {
    "inbound": {"website_form": 0.35, "demo_request": 0.25, "free_trial": 0.25, "content_download": 0.15},
    "outbound": {"outbound_email": 0.55, "referral": 0.15, "event": 0.15, "partner": 0.15},
}
INBOUND_SHARE = 0.60
PUBLIC_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]
PUBLIC_EMAIL_SHARE = 0.12

STAGES = ["qualified", "demo", "proposal", "negotiation"]
# P(advance to next stage); the rest are lost at that stage
ADVANCE = {"qualified": 0.78, "demo": 0.72, "proposal": 0.70, "negotiation": 0.78}
WEAK_REP_ADVANCE = {"proposal": 0.42, "negotiation": 0.50}
SLOW_FACTOR = {"demo": 0.85, "proposal": 0.75, "negotiation": 0.75}
STAGE_DAYS = {"qualified": (3, 12), "demo": (4, 15), "proposal": (5, 20), "negotiation": (4, 18)}
LOST_REASONS = {
    "qualified": ["Not a fit", "No budget", "No decision"],
    "demo": ["No budget", "Went with a competitor", "No decision", "Missing feature"],
    "proposal": ["Price", "Went with a competitor", "No decision", "Timing"],
    "negotiation": ["Price", "Went with a competitor", "Timing", "Legal / procurement"],
}

LABELS = ["interested", "not_now", "out_of_office", "referral", "unsubscribe"]
LABEL_P_DEAL = [0.45, 0.20, 0.20, 0.10, 0.05]
LABEL_P_NODEAL = [0.18, 0.30, 0.20, 0.10, 0.22]

REQUIRED_LEAD_FIELDS = ["company", "contact_name", "email", "industry", "company_size",
                        "region", "source", "owner_rep_id"]

# --------------------------------------------------------------------------- reply text

SUBJECTS = ["Quick question about {company}", "Cutting admin time at {company}",
            "{first}, a faster way to handle bookings", "Following up", "Your free trial",
            "Re: your demo request", "Ideas for {company}", "Getting paid faster at {company}",
            "Checking in", "The guide you downloaded"]
GREETINGS = ["Hi,", "Hi there,", "Hello,", "Hey,", "Thanks for reaching out.", "", "Good morning,"]
SIGNOFFS = ["Thanks,\n{first}", "Best,\n{first}", "Cheers,\n{first}", "{first}", "Regards,\n{full}",
            "Thanks\n{first} | {company}", "Sent from my phone"]

BODIES = {
    "interested": [
        "This looks useful. Can you send over pricing for a team of our size?",
        "Yes, happy to take a look. Do you have time Thursday afternoon for a quick call?",
        "We've been looking for something like this. What does onboarding involve?",
        "Interested. Can you show me how it handles recurring invoices?",
        "Good timing, our current setup is a mess. Send me a link to book a demo.",
        "Sounds promising. Does it integrate with QuickBooks? If so let's talk.",
        "I'd like to see a demo. Our office manager {colleague_first} should join too.",
        "Can we start a trial this week? I want to test it before the end of the month.",
        "Yes please. What's the earliest you can do a walkthrough?",
        "Ok you have my attention. How long does setup usually take for a business like {company}?",
        "Following up on my own reply, still keen. When can we chat?",
        "Send me the proposal and I'll run it past my partner.",
    ],
    "not_now": [
        "Not a priority right now. Maybe check back in the new year.",
        "We just signed a 12-month contract with another provider, so not for now.",
        "Thanks but our budget is frozen until Q2. Reach out then.",
        "Interesting, but we're in the middle of a move. Try me again in a couple of months.",
        "Not the right time for us. We're short-staffed and can't take on a new tool.",
        "Appreciate it, but we're happy with what we have for now.",
        "Can you follow up after our busy season? That ends around March.",
        "I like the idea but I can't look at this until after we hire a new manager.",
        "We're reviewing tools next quarter. Put me down for a follow-up then.",
        "No thanks for now, we're keeping things as they are this year.",
        "Too busy to think about this at the moment, sorry.",
    ],
    "out_of_office": [
        "I'm out of the office until {return_date} with limited access to email. For anything urgent please contact {colleague_full} at {colleague_email}.",
        "Automatic reply: I am on annual leave and will be back on {return_date}. I'll respond when I return.",
        "Thank you for your email. I'm away until {return_date}. Your message will not be forwarded.",
        "Out of office: I'm travelling this week and checking email occasionally. Back {return_date}.",
        "I am currently on parental leave. Please email {colleague_email} for anything that can't wait.",
        "Auto-reply: Our office is closed for the holiday and reopens {return_date}.",
        "Hi, I'm at a trade show until {return_date}. I'll get back to you after that.",
        "I'm out sick today and will reply as soon as I can.",
    ],
    "referral": [
        "I'm not the right person for this. {colleague_full} handles our software, you can reach them at {colleague_email}.",
        "Please speak to {colleague_first} ({colleague_email}), they make these decisions.",
        "Forwarding this to our operations manager, {colleague_full}. Copying them here: {colleague_email}",
        "Not my area. Try {colleague_email}, {colleague_first} looks after admin and billing.",
        "I left {company} last month. {colleague_full} has taken over, {colleague_email}.",
        "You want {colleague_first}, our office manager. {colleague_email}",
        "I don't buy tools but {colleague_full} might be interested. Their email is {colleague_email}.",
    ],
    "unsubscribe": [
        "Unsubscribe.",
        "Please remove me from your mailing list.",
        "Stop emailing me.",
        "Not interested. Please take me off your list.",
        "Remove.",
        "How did you get my email? Please delete it and don't contact me again.",
        "Please unsubscribe {email} from all future emails.",
        "Take us off your list, we're not interested and won't be.",
    ],
}

NICKNAMES = {"Jennifer": "Jen", "Michael": "Mike", "Robert": "Rob", "William": "Will",
             "Elizabeth": "Liz", "Christopher": "Chris", "Katherine": "Kate", "Matthew": "Matt",
             "Daniel": "Dan", "Joseph": "Joe", "Jessica": "Jess", "Thomas": "Tom",
             "Anthony": "Tony", "Samantha": "Sam", "Nicholas": "Nick", "Rebecca": "Becky",
             "Benjamin": "Ben", "Alexander": "Alex", "Patricia": "Pat", "Andrew": "Andy",
             "James": "Jim", "Richard": "Rick", "David": "Dave", "Steven": "Steve",
             "Timothy": "Tim", "Kimberly": "Kim", "Jonathan": "Jon", "Zachary": "Zach"}


# --------------------------------------------------------------------------- helpers

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def ts(d: datetime | None) -> str | None:
    return None if d is None else d.strftime("%Y-%m-%d %H:%M:%S")


def ds(d: date | None) -> str | None:
    return None if d is None else d.isoformat()


def pick(rng: np.random.Generator, options, weights=None):
    options = list(options)
    return options[int(rng.choice(len(options), p=weights))]


def choose(rng: np.random.Generator, pool, k: int, weights=None) -> list:
    pool = sorted(pool)
    if len(pool) < k:
        raise RuntimeError(f"need {k} records, only {len(pool)} eligible")
    p = None
    if weights is not None:
        w = np.array([weights[i] for i in pool], dtype=float)
        p = w / w.sum()
    return sorted(int(i) for i in rng.choice(pool, size=k, replace=False, p=p))


# --------------------------------------------------------------------------- generation

def make_reps(fake: Faker) -> pd.DataFrame:
    rows = []
    for rep_id, region in REPS:
        first, last = fake.first_name(), fake.last_name()
        rows.append({"rep_id": rep_id, "rep_name": f"{first} {last}", "region": region,
                     "email": f"{first.lower()}.{last.lower()}@ledgerloop.example"})
    return pd.DataFrame(rows)


def make_companies(rng, n):
    seen, out = set(), []
    industries = list(INDUSTRIES)
    while len(out) < n:
        industry = pick(rng, industries, INDUSTRY_WEIGHTS)
        core = pick(rng, PREFIXES) + pick(rng, SUFFIXES)
        word = pick(rng, INDUSTRIES[industry])
        key = (core, word)
        if key in seen:
            continue
        seen.add(key)
        out.append((f"{core} {word}{pick(rng, LEGAL, LEGAL_W)}", f"{slug(core)}{slug(word)}.com", industry))
    return out


def make_leads(fake, rng, reps):
    reps_by_region = {r: reps.loc[reps.region == r, "rep_id"].tolist() for r in REGIONS}
    companies = make_companies(rng, N_ORIGINAL_LEADS)
    names, rows = set(), []
    span = (AS_OF - START).days
    for i, (company, domain, industry) in enumerate(companies):
        while True:
            first, last = fake.first_name(), fake.last_name()
            if (first, last) not in names:
                names.add((first, last))
                break
        if rng.random() < PUBLIC_EMAIL_SHARE:
            email = f"{slug(first)}.{slug(last)}{int(rng.integers(1, 99))}@{pick(rng, PUBLIC_DOMAINS)}"
        else:
            email = f"{slug(first)}.{slug(last)}@{domain}"
        channel = "inbound" if rng.random() < INBOUND_SHARE else "outbound"
        src = SOURCES[channel]
        region = pick(rng, REGIONS, list(REGIONS.values()))
        day = START + timedelta(days=int(rng.integers(0, span)))
        created = datetime.combine(day, datetime.min.time()) + timedelta(
            hours=int(rng.integers(7, 19)), minutes=int(rng.integers(0, 60)))
        size = pick(rng, SIZES, [v[0] for v in SIZES.values()])
        rows.append({
            "lead_id": f"L{i + 1:05d}", "company": company, "contact_name": f"{first} {last}",
            "email": email, "industry": industry, "company_size": size, "region": region,
            "source": pick(rng, src, list(src.values())), "channel": channel,
            "created_at": created, "owner_rep_id": pick(rng, reps_by_region[region]),
        })
    return pd.DataFrame(rows)


def first_response_minutes(rng, channel, slow):
    if slow:
        return float(round(np.exp(rng.uniform(np.log(240), np.log(4320)))))
    if channel == "inbound":
        return float(min(45, max(1, round(rng.lognormal(np.log(9), 0.7)))))
    return float(rng.integers(0, 31))


def simulate_deal(rng, lead, deal_id, slow, weak):
    q = lead["created_at"].date() + timedelta(days=int(rng.integers(2, 22)))
    q = min(q, AS_OF - timedelta(days=2))
    median = SIZES[lead["company_size"]][1]
    amount = float(round(rng.lognormal(np.log(median), 0.45) / 50) * 50)
    dates = {"qualified": q}
    stage, t, closed, won, lost_reason, lost_at = "qualified", q, None, False, None, None
    while True:
        lo, hi = STAGE_DAYS[stage]
        nxt = t + timedelta(days=int(rng.integers(lo, hi + 1)))
        if nxt > AS_OF:
            break
        p = ADVANCE[stage]
        if weak and stage in WEAK_REP_ADVANCE:
            p = WEAK_REP_ADVANCE[stage]
        if slow and stage in SLOW_FACTOR:
            p *= SLOW_FACTOR[stage]
        if rng.random() < p:
            idx = STAGES.index(stage)
            if idx == len(STAGES) - 1:
                closed, won, stage = nxt, True, "won"
                break
            stage, t = STAGES[idx + 1], nxt
            dates[stage] = t
        else:
            closed, lost_at, stage = nxt, stage, "lost"
            lost_reason = pick(rng, LOST_REASONS[lost_at])
            break
    if closed is None:
        entered = dates[stage]
        last = AS_OF - timedelta(days=int(rng.integers(0, min(20, (AS_OF - entered).days) + 1)))
    else:
        last = closed
    return {
        "deal_id": deal_id, "lead_id": lead["lead_id"], "company": lead["company"],
        "owner_rep_id": lead["owner_rep_id"], "region": lead["region"], "amount_usd": amount,
        "stage": stage, "is_closed": closed is not None, "is_won": won,
        "qualified_date": dates.get("qualified"), "demo_date": dates.get("demo"),
        "proposal_date": dates.get("proposal"), "negotiation_date": dates.get("negotiation"),
        "closed_date": closed, "last_activity_date": last,
        "lost_reason": lost_reason, "lost_at_stage": lost_at,
    }


def fuzzy_variant(rng, company, name, email):
    """Same person and company, written differently. Never identical on email."""
    first, last = name.split(" ", 1)
    base = re.sub(r"( LLC| Co\.| Inc\.)$", "", company)
    company_opts = [base.upper(), base.lower(), base + ", Inc.", base + " LLC", base,
                    re.sub(r"^(\w+?)(field|leaf|wood|stone|brook|gate|haven|crest|side|view|point|line|well|worth|ford|light|bridge|dale|wick|spring|rock|vale|wind|more|ton)\b",
                           lambda m: f"{m.group(1)} {m.group(2).capitalize()}", base)]
    company2 = pick(rng, [c for c in company_opts if c != company] or [base + " LLC"])
    name_opts = [f"{NICKNAMES.get(first, first)} {last}", f"{first[0]}. {last}", name.lower(),
                 f"{first} {last[:-2] + last[-1] + last[-2]}" if len(last) > 3 else name.upper()]
    name2 = pick(rng, [n for n in name_opts if n != name] or [name.upper()])
    local, domain = email.split("@")
    email_opts = [email.upper(), email.capitalize(), local.replace(".", "") + "@" + domain,
                  f"{slug(first)[0]}{slug(last)}@{domain}"]
    email2 = pick(rng, [e for e in email_opts if e != email])
    return company2, name2, email2


def make_reply_text(rng, fake, label, lead, reply_date):
    first, last = lead["contact_name"].split(" ", 1)
    domain = lead["email"].split("@")[1]
    c_first, c_last = fake.first_name(), fake.last_name()
    if domain in PUBLIC_DOMAINS:
        c_email = f"{slug(c_first)}.{slug(c_last)}@{pick(rng, PUBLIC_DOMAINS)}"
    else:
        c_email = f"{slug(c_first)}.{slug(c_last)}@{domain}"
    back = reply_date + timedelta(days=int(rng.integers(2, 15)))
    fill = {"first": first, "full": lead["contact_name"], "company": lead["company"],
            "email": lead["email"], "colleague_first": c_first,
            "colleague_full": f"{c_first} {c_last}", "colleague_email": c_email,
            "return_date": back.strftime("%A %B %-d") if sys.platform != "win32" else back.strftime("%A %B %#d")}
    body = pick(rng, BODIES[label]).format(**fill)
    if label == "out_of_office":
        return "Automatic reply: " + pick(rng, SUBJECTS).format(**fill), body
    greet = pick(rng, GREETINGS)
    sign = pick(rng, SIGNOFFS).format(**fill)
    if label == "unsubscribe" and rng.random() < 0.5:
        greet, sign = "", ""
    text = "\n\n".join(p for p in [greet, body, sign] if p)
    return "Re: " + pick(rng, SUBJECTS).format(**fill), text


def generate():
    fake = Faker("en_US")
    fake.seed_instance(SEED)
    rng = np.random.default_rng(SEED)

    reps = make_reps(fake)
    leads = make_leads(fake, rng, reps)
    n = len(leads)
    idx = list(range(n))
    inbound = [i for i in idx if leads.at[i, "channel"] == "inbound"]
    old_enough = {i for i in idx if leads.at[i, "created_at"].date() <= AS_OF - timedelta(days=NEVER_MIN_AGE_DAYS)}

    # --- planted lead-level sets (kept disjoint so each count is unambiguous)
    never = set(choose(rng, [i for i in inbound if i in old_enough], N_NEVER))
    slow_ok = {i for i in idx if leads.at[i, "created_at"].date() <= AS_OF - timedelta(days=4)}
    slow = set(choose(rng, [i for i in inbound if i not in never and i in slow_ok], N_SLOW))

    minutes, first_resp = [], []
    for i in idx:
        if i in never:
            minutes.append(np.nan)
            first_resp.append(None)
            continue
        m = first_response_minutes(rng, leads.at[i, "channel"], i in slow)
        minutes.append(m)
        first_resp.append(leads.at[i, "created_at"] + timedelta(minutes=m))
    leads["first_response_minutes"] = minutes
    leads["first_response_at"] = first_resp

    # --- deals
    deal_pool = [i for i in idx if i not in never
                 and leads.at[i, "created_at"].date() <= AS_OF - timedelta(days=14)]
    weights = {i: (0.8 if i in slow else 1.0) for i in deal_pool}
    deal_leads = choose(rng, deal_pool, N_DEALS_BASE, weights)
    deals = []
    for k, i in enumerate(deal_leads):
        lead = leads.iloc[i]
        deals.append(simulate_deal(rng, lead, f"D{k + 1:04d}", i in slow, lead["owner_rep_id"] == WEAK_REP))
    deals = pd.DataFrame(deals)

    # --- stalled proposals: reached proposal long enough ago, not the weak rep
    cand = deals.index[(deals.proposal_date.notna())
                       & (deals.proposal_date.apply(lambda d: pd.notna(d) and d <= AS_OF - timedelta(days=STALL_DAYS + 10)))
                       & (deals.owner_rep_id != WEAK_REP)].tolist()
    stalled = choose(rng, cand, N_STALLED)
    for j in stalled:
        p = deals.at[j, "proposal_date"]
        gap = (AS_OF - timedelta(days=STALL_DAYS) - p).days
        deals.loc[j, ["stage", "is_closed", "is_won"]] = ["proposal", False, False]
        for col in ["negotiation_date", "closed_date", "lost_reason", "lost_at_stage"]:
            deals.at[j, col] = None
        deals.at[j, "last_activity_date"] = p + timedelta(days=int(rng.integers(0, gap + 1)))

    # --- duplicates
    lead_has_deal = dict(zip(deals.lead_id, deals.index))
    open_deal_leads = [i for i in deal_leads if i not in slow
                       and not deals.at[lead_has_deal[leads.at[i, "lead_id"]], "is_closed"]
                       and lead_has_deal[leads.at[i, "lead_id"]] not in stalled]
    dupe_deal_src = choose(rng, open_deal_leads, N_DUPE_DEALS)
    other_src_pool = [i for i in idx if i not in never and i not in slow and i not in dupe_deal_src]
    other_src = choose(rng, other_src_pool, N_EXACT_DUPES + N_FUZZY_DUPES - N_DUPE_DEALS)
    all_src = dupe_deal_src + other_src
    order = list(rng.permutation(len(all_src)))
    dup_types = {all_src[o]: ("exact" if r < N_EXACT_DUPES else "fuzzy") for r, o in enumerate(order)}

    dupe_rows, dupe_pairs, dupe_deals = [], [], []
    next_lead = n + 1
    for src in sorted(all_src):
        s = leads.iloc[src]
        kind = dup_types[src]
        if kind == "exact":
            company, name, email = s.company, s.contact_name, s.email
        else:
            company, name, email = fuzzy_variant(rng, s.company, s.contact_name, s.email)
        created = s.created_at + timedelta(days=int(rng.integers(3, 61)), hours=int(rng.integers(0, 5)))
        latest = datetime.combine(AS_OF - timedelta(days=1), datetime.min.time()) + timedelta(hours=17)
        created = min(created, latest)
        channel = "inbound" if rng.random() < 0.6 else "outbound"
        src_opts = SOURCES[channel]
        owner_opts = reps.loc[reps.region == s.region, "rep_id"].tolist()
        m = first_response_minutes(rng, channel, False)
        lead_id = f"L{next_lead:05d}"
        next_lead += 1
        dupe_rows.append({
            "lead_id": lead_id, "company": company, "contact_name": name, "email": email,
            "industry": s.industry, "company_size": s.company_size, "region": s.region,
            "source": pick(rng, src_opts, list(src_opts.values())), "channel": channel,
            "created_at": created, "owner_rep_id": pick(rng, owner_opts),
            "first_response_minutes": m, "first_response_at": created + timedelta(minutes=m),
        })
        dupe_pairs.append({"duplicate_lead_id": lead_id, "original_lead_id": s.lead_id, "match_type": kind})
        if src in dupe_deal_src:
            od = deals.loc[lead_has_deal[s.lead_id]]
            q = max(created.date() + timedelta(days=int(rng.integers(1, 6))), od.qualified_date)
            q = min(q, AS_OF - timedelta(days=1))
            dupe_deals.append({
                "deal_id": None, "lead_id": lead_id, "company": company,
                "owner_rep_id": dupe_rows[-1]["owner_rep_id"], "region": s.region,
                "amount_usd": od.amount_usd, "stage": "qualified", "is_closed": False, "is_won": False,
                "qualified_date": q, "demo_date": None, "proposal_date": None, "negotiation_date": None,
                "closed_date": None, "last_activity_date": AS_OF - timedelta(days=int(rng.integers(0, min(20, (AS_OF - q).days) + 1))),
                "lost_reason": None, "lost_at_stage": None,
            })
    leads = pd.concat([leads, pd.DataFrame(dupe_rows)], ignore_index=True)
    dupe_deals = pd.DataFrame(dupe_deals)
    dupe_deals["deal_id"] = [f"D{len(deals) + k + 1:04d}" for k in range(len(dupe_deals))]
    deals = pd.concat([deals, dupe_deals], ignore_index=True)
    dupe_lead_ids = {p["duplicate_lead_id"] for p in dupe_pairs}

    # --- replies (from contacted, non-duplicate leads that will keep their email)
    deal_lead_ids = set(deals.lead_id)
    missing_pool = [i for i in idx if i not in never and i not in slow and i not in all_src
                    and leads.at[i, "lead_id"] not in deal_lead_ids]
    missing_all = choose(rng, missing_pool, sum(MISSING.values()))
    order = list(rng.permutation(len(missing_all)))
    missing_field, cursor = {}, 0
    for field, k in MISSING.items():
        for o in order[cursor:cursor + k]:
            missing_field[missing_all[o]] = field
        cursor += k

    reply_pool = [i for i in idx if i not in never and missing_field.get(i) != "email"]
    rw = np.array([1.5 if leads.at[i, "lead_id"] in deal_lead_ids else 1.0 for i in reply_pool])
    reply_leads = rng.choice(reply_pool, size=N_REPLIES, replace=True, p=rw / rw.sum())
    replies = []
    for k, i in enumerate(sorted(int(x) for x in reply_leads)):
        lead = leads.iloc[i]
        has_deal = lead["lead_id"] in deal_lead_ids
        label = pick(rng, LABELS, LABEL_P_DEAL if has_deal else LABEL_P_NODEAL)
        start = lead["first_response_at"]
        end = min(start + timedelta(days=45), datetime.combine(AS_OF, datetime.min.time()) + timedelta(hours=18))
        span = max(1, int((end - start).total_seconds() // 60))
        received = start + timedelta(minutes=int(rng.integers(1, span + 1)))
        subject, text = make_reply_text(rng, fake, label, lead, received.date())
        replies.append({"reply_id": None, "lead_id": lead["lead_id"], "from_email": lead["email"],
                        "received_at": received, "subject": subject, "reply_text": text, "true_label": label})
    replies = pd.DataFrame(replies).sort_values(["received_at", "lead_id"]).reset_index(drop=True)
    replies["reply_id"] = [f"E{k + 1:05d}" for k in range(len(replies))]

    # --- lead status and last activity
    last_reply = replies.groupby("lead_id").received_at.max()
    unsubscribed = set(replies.loc[replies.true_label == "unsubscribe", "lead_id"])
    deal_last = deals.groupby("lead_id").last_activity_date.max()
    status, last_act = [], []
    for _, r in leads.iterrows():
        if pd.isna(r.first_response_minutes):
            status.append("new")
            last_act.append(None)
            continue
        acts = [r.first_response_at.date()]
        if r.lead_id in last_reply.index:
            acts.append(last_reply[r.lead_id].date())
        if r.lead_id in deal_last.index:
            acts.append(deal_last[r.lead_id])
        last_act.append(max(acts))
        if r.lead_id in deal_lead_ids:
            status.append("converted")
        elif r.lead_id in unsubscribed:
            status.append("unsubscribed")
        else:
            status.append(pick(rng, ["contacted", "nurturing", "unqualified"], [0.5, 0.3, 0.2]))
    leads["status"] = status
    leads["last_activity_date"] = last_act

    # --- blank the planted missing fields last, so nothing above depended on them
    for i, field in missing_field.items():
        leads.at[i, field] = None

    leads = leads.sort_values("lead_id").reset_index(drop=True)
    planted = {
        "never": {f"L{i + 1:05d}" for i in never},
        "slow": {f"L{i + 1:05d}" for i in slow},
        "stalled": set(deals.loc[stalled, "deal_id"]),
        "dupe_pairs": dupe_pairs,
        "dupe_deals": set(dupe_deals.deal_id),
        "missing": {f"L{i + 1:05d}": f for i, f in missing_field.items()},
    }
    return reps, leads, deals, replies, planted


# --------------------------------------------------------------------------- stage log

def stage_history(deals: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, d in deals.iterrows():
        for stage in STAGES:
            v = d[f"{stage}_date"]
            if v is not None and not pd.isna(v):
                rows.append({"deal_id": d.deal_id, "stage": stage, "entered_date": v})
        if d.is_closed:
            rows.append({"deal_id": d.deal_id, "stage": "won" if d.is_won else "lost", "entered_date": d.closed_date})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- answer key

def _days_since(d) -> int:
    return (AS_OF - pd.to_datetime(d).date()).days


def answer_key(reps, leads, deals, replies, planted):
    """Measure every leak from the finished data, using the documented definitions."""
    won = deals[deals.is_won]
    avg_won = float(won.amount_usd.mean())
    dupe_ids = {p["duplicate_lead_id"] for p in planted["dupe_pairs"]}
    worked = leads[leads.first_response_minutes.notna() & ~leads.lead_id.isin(dupe_ids)]
    value_per_lead = float(won.amount_usd.sum()) / len(worked)

    # 1 stalled proposals
    st = deals[(deals.stage == "proposal") & (deals.last_activity_date.apply(_days_since) >= STALL_DAYS)]
    assert set(st.deal_id) == planted["stalled"], "stalled mismatch"
    open_other = deals[~deals.is_closed & ~deals.deal_id.isin(st.deal_id)]
    assert (open_other.last_activity_date.apply(_days_since) < STALL_DAYS).all()

    # 2 slow first response (inbound)
    inbound = leads[leads.channel == "inbound"]
    slow = inbound[inbound.first_response_minutes > SLOW_MINUTES]
    assert set(slow.lead_id) == planted["slow"], "slow mismatch"
    ld = deals.merge(leads[["lead_id", "channel", "first_response_minutes"]], on="lead_id")
    closed_in = ld[(ld.channel == "inbound") & ld.is_closed]
    s_closed = closed_in[closed_in.first_response_minutes > SLOW_MINUTES]
    f_closed = closed_in[closed_in.first_response_minutes <= SLOW_MINUTES]
    s_rate, f_rate = s_closed.is_won.mean(), f_closed.is_won.mean()
    slow_excess = len(s_closed) * (f_rate - s_rate)

    # 3 duplicates
    key = ["company", "contact_name", "email"]
    exact_found = leads[leads.duplicated(subset=key, keep="first") & leads[key].notna().all(axis=1)]
    exact_planted = {p["duplicate_lead_id"] for p in planted["dupe_pairs"] if p["match_type"] == "exact"}
    assert set(exact_found.lead_id) == exact_planted, "exact dupe mismatch"
    dd = deals[deals.lead_id.isin(dupe_ids)]
    assert set(dd.deal_id) == planted["dupe_deals"]
    n_fuzzy = sum(p["match_type"] == "fuzzy" for p in planted["dupe_pairs"])

    # 4 missing required fields
    miss_mask = leads[REQUIRED_LEAD_FIELDS].isna()
    miss_rows = leads[miss_mask.any(axis=1)]
    assert set(miss_rows.lead_id) == set(planted["missing"]), "missing mismatch"
    per_field = {f: int(miss_mask[f].sum()) for f in REQUIRED_LEAD_FIELDS if miss_mask[f].sum()}

    # 5 never followed up
    never = leads[leads.first_response_at.isna()]
    assert set(never.lead_id) == planted["never"], "never mismatch"

    # 6 weak rep, proposal -> won on closed deals that reached proposal
    reached = deals[deals.proposal_date.notna() & deals.is_closed]
    by_rep = reached.groupby("owner_rep_id").is_won.agg(["sum", "count"])
    by_rep["rate"] = by_rep["sum"] / by_rep["count"]
    worst = by_rep.rate.idxmin()
    assert worst == WEAK_REP, f"weakest rep is {worst}, expected {WEAK_REP}"
    others = reached[reached.owner_rep_id != WEAK_REP]
    o_rate = others.is_won.mean()
    w = by_rep.loc[WEAK_REP]
    rep_excess = w["count"] * (o_rate - w["rate"])

    r = lambda x: round(float(x), 2)
    leaks = [
        {"id": "stalled_proposals", "name": "Deals stalled at proposal",
         "entity": "deals", "count": len(st), "usd": r(st.amount_usd.sum()), "usd_basis": "measured",
         "definition": f"stage == 'proposal' and days from last_activity_date to {AS_OF} >= {STALL_DAYS}",
         "usd_formula": "sum(amount_usd) of those deals (pipeline at risk)"},
        {"id": "slow_first_response", "name": "Slow first response on inbound leads",
         "entity": "leads", "count": len(slow), "usd": r(slow_excess * avg_won), "usd_basis": "estimated",
         "definition": f"channel == 'inbound' and first_response_minutes > {SLOW_MINUTES}",
         "usd_formula": "closed slow-lead deals x (fast win rate - slow win rate) x average won amount",
         "detail": {"slow_closed_deals": len(s_closed), "slow_won": int(s_closed.is_won.sum()),
                    "slow_win_rate": r(s_rate), "fast_closed_deals": len(f_closed),
                    "fast_won": int(f_closed.is_won.sum()), "fast_win_rate": r(f_rate),
                    "excess_lost_deals": r(slow_excess)}},
        {"id": "duplicate_leads", "name": "Duplicate leads (exact + fuzzy)",
         "entity": "leads", "count": len(dupe_ids), "usd": r(dd.amount_usd.sum()), "usd_basis": "measured",
         "definition": "a later lead row for the same person at the same company; pairs in answer_key/duplicate_pairs.csv",
         "usd_formula": "sum(amount_usd) of open deals opened on a duplicate lead (pipeline counted twice)",
         "detail": {"exact": len(exact_found), "fuzzy": n_fuzzy, "duplicate_deals": len(dd)}},
        {"id": "missing_required_fields", "name": "Leads missing a required field",
         "entity": "leads", "count": len(miss_rows), "usd": r(per_field.get("email", 0) * value_per_lead),
         "usd_basis": "estimated",
         "definition": f"any of {', '.join(REQUIRED_LEAD_FIELDS)} is empty",
         "usd_formula": "leads missing email (cannot be worked) x value per worked lead; other fields are a routing/reporting cost, valued at $0",
         "detail": {"by_field": per_field}},
        {"id": "never_followed_up", "name": "Leads never followed up",
         "entity": "leads", "count": len(never), "usd": r(len(never) * value_per_lead), "usd_basis": "estimated",
         "definition": "first_response_at is empty (all are inbound and at least 7 days old)",
         "usd_formula": "count x value per worked lead"},
        {"id": "weak_rep_proposal_conversion", "name": f"Rep {WEAK_REP} proposal-to-won rate",
         "entity": "deals", "count": int(w["count"]), "usd": r(rep_excess * avg_won), "usd_basis": "estimated",
         "definition": "closed deals that reached proposal, owned by the rep; rate = won / closed",
         "usd_formula": "rep's closed post-proposal deals x (other reps' rate - rep's rate) x average won amount",
         "detail": {"rep_id": WEAK_REP, "rep_won": int(w["sum"]), "rep_rate": r(w["rate"]),
                    "other_reps_rate": r(o_rate),
                    "by_rep": {k: {"won": int(v["sum"]), "closed": int(v["count"]), "rate": r(v["rate"])}
                               for k, v in by_rep.iterrows()}}},
    ]
    totals = {
        "as_of": AS_OF.isoformat(), "seed": SEED,
        "rows": {"reps": len(reps), "leads": len(leads), "deals": len(deals), "replies": len(replies)},
        "average_won_amount_usd": r(avg_won),
        "value_per_worked_lead_usd": r(value_per_lead),
        "won_deals": len(won), "won_usd": r(won.amount_usd.sum()),
        "open_pipeline_usd": r(deals[~deals.is_closed].amount_usd.sum()),
        "reply_labels": {k: int(v) for k, v in replies.true_label.value_counts().sort_index().items()},
        "total_leak_usd": r(sum(l["usd"] for l in leaks)),
    }
    records = []
    for d in sorted(st.deal_id):
        records.append(("stalled_proposals", "deal", d, ""))
    for l in sorted(slow.lead_id):
        records.append(("slow_first_response", "lead", l, ""))
    for p in sorted(planted["dupe_pairs"], key=lambda p: p["duplicate_lead_id"]):
        records.append(("duplicate_leads", "lead", p["duplicate_lead_id"], p["match_type"]))
    for d in sorted(dd.deal_id):
        records.append(("duplicate_leads", "deal", d, "deal on duplicate lead"))
    for l, f in sorted(planted["missing"].items()):
        records.append(("missing_required_fields", "lead", l, f))
    for l in sorted(never.lead_id):
        records.append(("never_followed_up", "lead", l, ""))
    records = pd.DataFrame(records, columns=["leak_id", "record_type", "record_id", "note"])
    return {"summary": totals, "leaks": leaks}, records


# --------------------------------------------------------------------------- output

def fmt_usd(x: float) -> str:
    return f"${x:,.0f}"


def leak_table_md(key) -> str:
    s = key["summary"]
    lines = [
        f"_Generated by `data/generate.py` (seed {s['seed']}, snapshot {s['as_of']}). Do not edit by hand._",
        "",
        f"Rows: **{s['rows']['leads']:,} leads**, **{s['rows']['deals']} deals**, "
        f"**{s['rows']['replies']:,} replies**, {s['rows']['reps']} reps.",
        "",
        "| # | Leak | Entity | Count | USD | Basis | How USD is worked out |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for n, l in enumerate(key["leaks"], 1):
        lines.append(f"| {n} | {l['name']} (`{l['id']}`) | {l['entity']} | {l['count']:,} | "
                     f"{fmt_usd(l['usd'])} | {l['usd_basis']} | {l['usd_formula']} |")
    lines.append(f"| | **Total** | | | **{fmt_usd(s['total_leak_usd'])}** | | |")
    d = {l["id"]: l.get("detail", {}) for l in key["leaks"]}
    sl, du, mi, wr = d["slow_first_response"], d["duplicate_leads"], d["missing_required_fields"], d["weak_rep_proposal_conversion"]
    lines += [
        "",
        "Supporting numbers:",
        "",
        f"- Average won deal: {fmt_usd(s['average_won_amount_usd'])}. Value per worked lead "
        f"(won USD / leads with a first response, duplicates excluded): {fmt_usd(s['value_per_worked_lead_usd'])}.",
        f"- Won: {s['won_deals']} deals, {fmt_usd(s['won_usd'])}. Open pipeline: {fmt_usd(s['open_pipeline_usd'])}.",
        f"- Slow vs fast inbound win rate (closed deals): {sl['slow_win_rate']:.0%} "
        f"({sl['slow_won']}/{sl['slow_closed_deals']}) vs {sl['fast_win_rate']:.0%} "
        f"({sl['fast_won']}/{sl['fast_closed_deals']}); {sl['excess_lost_deals']} excess lost deals.",
        f"- Duplicates: {du['exact']} exact, {du['fuzzy']} fuzzy; {du['duplicate_deals']} of them carry a second open deal.",
        "- Missing fields: " + ", ".join(f"{k} {v}" for k, v in mi["by_field"].items()) + " (one field per lead).",
        f"- Proposal-to-won by rep (closed deals that reached proposal): "
        + ", ".join(f"{k} {v['rate']:.0%} ({v['won']}/{v['closed']})" for k, v in wr["by_rep"].items())
        + f". {wr['rep_id']} vs everyone else: {wr['rep_rate']:.0%} vs {wr['other_reps_rate']:.0%}.",
        "- Reply labels: " + ", ".join(f"{k} {v}" for k, v in s["reply_labels"].items()) + ".",
    ]
    return "\n".join(lines)


def to_csv(df: pd.DataFrame, path: Path, datetime_cols=(), date_cols=()):
    out = df.copy()
    for c in datetime_cols:
        out[c] = out[c].apply(lambda v: ts(v) if v is not None and not pd.isna(v) else None)
    for c in date_cols:
        out[c] = out[c].apply(lambda v: ds(v) if v is not None and not pd.isna(v) else None)
    out.to_csv(path, index=False, lineterminator="\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default data/sample)")
    args = ap.parse_args()
    out = args.out.resolve()
    (out / "answer_key").mkdir(parents=True, exist_ok=True)

    reps, leads, deals, replies, planted = generate()
    key, records = answer_key(reps, leads, deals, replies, planted)

    lead_cols = ["lead_id", "company", "contact_name", "email", "industry", "company_size", "region",
                 "source", "channel", "created_at", "owner_rep_id", "first_response_at",
                 "first_response_minutes", "last_activity_date", "status"]
    deal_cols = ["deal_id", "lead_id", "company", "owner_rep_id", "region", "amount_usd", "stage",
                 "is_closed", "is_won", "qualified_date", "demo_date", "proposal_date",
                 "negotiation_date", "closed_date", "last_activity_date", "lost_reason", "lost_at_stage"]
    date_deal = ["qualified_date", "demo_date", "proposal_date", "negotiation_date", "closed_date", "last_activity_date"]
    leads_out = leads[lead_cols].copy()
    leads_out["first_response_minutes"] = leads_out.first_response_minutes.astype("Int64")
    to_csv(reps, out / "reps.csv")
    to_csv(leads_out, out / "leads.csv", ["created_at", "first_response_at"], ["last_activity_date"])
    deals_out = deals[deal_cols].copy()
    deals_out["amount_usd"] = deals_out.amount_usd.astype(int)
    to_csv(deals_out, out / "deals.csv", date_cols=date_deal)
    to_csv(stage_history(deals), out / "deal_stage_history.csv", date_cols=["entered_date"])
    to_csv(replies[["reply_id", "lead_id", "from_email", "received_at", "subject", "reply_text", "true_label"]],
           out / "replies.csv", ["received_at"])
    pd.DataFrame(planted["dupe_pairs"]).sort_values("duplicate_lead_id").to_csv(
        out / "answer_key" / "duplicate_pairs.csv", index=False, lineterminator="\n")
    records.to_csv(out / "answer_key" / "planted_records.csv", index=False, lineterminator="\n")
    (out / "answer_key" / "leak_summary.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")

    table = leak_table_md(key)
    print(table)
    print(f"\nWrote {out}")
    if out == DEFAULT_OUT.resolve() and SCHEMA_MD.exists():
        text = SCHEMA_MD.read_text(encoding="utf-8")
        start, end = "<!-- LEAKS:START -->", "<!-- LEAKS:END -->"
        if start in text and end in text:
            pre, rest = text.split(start, 1)
            _, post = rest.split(end, 1)
            SCHEMA_MD.write_text(f"{pre}{start}\n{table}\n{end}{post}", encoding="utf-8", newline="\n")
            print(f"Updated leak table in {SCHEMA_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
