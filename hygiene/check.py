"""CRM hygiene check: find revenue leaks in the CRM export and put a USD value on each.

Reads only the CRM tables in data/sample/ (leads, deals, reps). It never reads
data/sample/answer_key/; that folder is used by hygiene/evaluate.py to score this file afterwards.

Run from the repo root:
    python hygiene/check.py

Writes:
    hygiene/output/findings.csv   one row per flagged record: check, record_id, detail, usd_at_risk
    hygiene/output/summary.json   per check: count and usd, plus the grand total

Standard library + pandas only, so the dashboard needs no extra install.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "sample"
OUT = ROOT / "hygiene" / "output"

AS_OF = date(2026, 10, 31)

# --- Business rules. These are policy choices a RevOps team would set, not values read from the
# --- answer key. Each one is stated in hygiene/README.md.
REQUIRED_FIELDS = ["company", "contact_name", "email", "industry",
                   "company_size", "region", "source", "owner_rep_id"]
STALE_DAYS = 30               # proposal with no activity for this long is stalled
SLOW_RESPONSE_MINUTES = 60    # inbound speed-to-lead SLA
FOLLOW_UP_GRACE_DAYS = 7      # a lead younger than this may simply not be worked yet
REP_OUTLIER_Z = -3.0          # one-sided two-proportion z-score vs the rest of the team
NAME_SIMILARITY = 0.80        # last-name similarity needed for a fuzzy duplicate (0-1)

PUBLIC_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
                  "aol.com", "live.com", "msn.com", "protonmail.com", "me.com"}
LEGAL_SUFFIXES = {"llc", "inc", "co", "corp", "corporation", "ltd", "limited", "company",
                  "pty", "plc", "gmbh"}
# Common English nicknames. Small on purpose: a long table raises false matches.
NICKNAMES = {
    "mike": "michael", "mick": "michael", "rob": "robert", "bob": "robert", "bobby": "robert",
    "bill": "william", "will": "william", "liz": "elizabeth", "beth": "elizabeth",
    "jim": "james", "jimmy": "james", "dave": "david", "steve": "steven", "stephen": "steven",
    "chris": "christopher", "matt": "matthew", "dan": "daniel", "danny": "daniel",
    "tom": "thomas", "tony": "anthony", "kate": "katherine", "katie": "katherine",
    "jess": "jessica", "jen": "jennifer", "jenny": "jennifer", "nick": "nicholas",
    "sam": "samuel", "alex": "alexander", "andy": "andrew", "joe": "joseph", "greg": "gregory",
    "ben": "benjamin", "pat": "patricia", "sue": "susan", "rick": "richard", "dick": "richard",
    "ed": "edward", "ted": "edward", "jon": "jonathan", "josh": "joshua", "tim": "timothy",
    "ken": "kenneth", "ron": "ronald", "don": "donald", "larry": "lawrence", "jeff": "jeffrey",
}


# ----------------------------------------------------------------------------- loading

def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    leads = pd.read_csv(DATA / "leads.csv", dtype=str, keep_default_na=False, na_values=[""])
    deals = pd.read_csv(DATA / "deals.csv", dtype=str, keep_default_na=False, na_values=[""])
    leads["created_at"] = pd.to_datetime(leads["created_at"])
    leads["first_response_at"] = pd.to_datetime(leads["first_response_at"])
    leads["first_response_minutes"] = pd.to_numeric(leads["first_response_minutes"])
    deals["amount_usd"] = pd.to_numeric(deals["amount_usd"])
    for col in ("is_closed", "is_won"):
        deals[col] = deals[col] == "True"
    for col in ("proposal_date", "closed_date", "last_activity_date"):
        deals[col] = pd.to_datetime(deals[col])
    return leads, deals


# ----------------------------------------------------------------------------- normalising

def norm_company(name) -> str:
    """'Lark Well Clinic, Inc.' -> 'larkwellclinic'. Drops case, punctuation, legal suffixes and
    spaces, so split compound names collapse onto the original."""
    if not isinstance(name, str):
        return ""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def email_parts(email) -> tuple[str, str]:
    if not isinstance(email, str) or "@" not in email:
        return "", ""
    local, domain = email.lower().rsplit("@", 1)
    return local, domain


def name_tokens(name) -> tuple[str, str]:
    """('first', 'last') in lower case with dots dropped. 'N. Galloway' -> ('n', 'galloway')."""
    if not isinstance(name, str):
        return "", ""
    parts = re.sub(r"[^a-z ]", " ", name.lower()).split()
    if not parts:
        return "", ""
    return parts[0], parts[-1]


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def first_names_compatible(a: str, b: str) -> bool:
    """Same first name, an initial, a prefix ('Steve'/'Steven') or a known nickname."""
    if not a or not b or a[0] != b[0]:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    ca, cb = NICKNAMES.get(a, a), NICKNAMES.get(b, b)
    return ca == cb or similar(a, b) >= 0.85


def same_person(a: pd.Series, b: pd.Series, name_threshold: float) -> tuple[bool, str]:
    """Is lead b the same person as lead a? Both are already known to share a company key or a
    company email domain. Returns (match, reason)."""
    la, da = email_parts(a["email"])
    lb, db = email_parts(b["email"])
    fa, sa = name_tokens(a["contact_name"])
    fb, sb = name_tokens(b["contact_name"])
    # 1) identical mailbox (ignoring case and dots in the local part). Safe on public domains too,
    #    because it is the whole address, not just the domain.
    if la and la.replace(".", "") == lb.replace(".", "") and da == db:
        return True, "same email address"
    # 2) the names agree: similar surname and a compatible first name.
    if similar(sa, sb) >= name_threshold and first_names_compatible(fa, fb):
        return True, f"name match ({a['contact_name']} ~ {b['contact_name']})"
    return False, ""


# ----------------------------------------------------------------------------- checks

@dataclass
class Finding:
    check: str
    record_id: str
    detail: str
    usd_at_risk: float


def find_duplicates(leads: pd.DataFrame, name_threshold: float = NAME_SIMILARITY) -> pd.DataFrame:
    """Return one row per duplicate lead: duplicate_lead_id, original_lead_id, match_type, reason.

    Blocking keeps this fast and safe: two leads are only compared if they share a normalised
    company name or a company (non-public) email domain. A public domain such as gmail.com is never
    a block on its own. Within a block the earliest lead_id is the original."""
    df = leads.copy()
    df["company_key"] = df["company"].map(norm_company)
    df["domain"] = df["email"].map(lambda e: email_parts(e)[1])
    df = df.sort_values("lead_id").reset_index(drop=True)

    candidates: set[tuple[int, int]] = set()
    for key, col in (("company_key", "company_key"), ("domain", "domain")):
        groups = df[df[col] != ""]
        if key == "domain":
            groups = groups[~groups["domain"].isin(PUBLIC_DOMAINS)]
        for _, idx in groups.groupby(col).groups.items():
            idx = sorted(idx)
            for i in range(len(idx)):
                for j in range(i + 1, len(idx)):
                    candidates.add((idx[i], idx[j]))

    original_of: dict[int, tuple[int, str]] = {}
    for i, j in sorted(candidates):
        if j in original_of:
            continue
        a, b = df.loc[i], df.loc[j]
        # A different company with the same email domain is not a duplicate by itself; the
        # company key must agree too (covers 'Lark Well Clinic' vs 'Larkwell Clinic Inc.').
        if a["company_key"] != b["company_key"] and similar(a["company_key"], b["company_key"]) < 0.9:
            continue
        ok, reason = same_person(a, b, name_threshold)
        if ok:
            root = original_of.get(i, (i, ""))[0]   # chain to the first lead in the cluster
            original_of[j] = (root, reason)

    rows = []
    for j, (i, reason) in sorted(original_of.items()):
        a, b = df.loc[i], df.loc[j]
        exact = all(a[c] == b[c] for c in ("company", "contact_name", "email"))
        rows.append({"duplicate_lead_id": b["lead_id"], "original_lead_id": a["lead_id"],
                     "match_type": "exact" if exact else "fuzzy",
                     "reason": "identical company, name and email" if exact else reason})
    return pd.DataFrame(rows, columns=["duplicate_lead_id", "original_lead_id", "match_type", "reason"])


def run(leads: pd.DataFrame, deals: pd.DataFrame,
        name_threshold: float = NAME_SIMILARITY) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    as_of = pd.Timestamp(AS_OF)
    open_deals = deals[~deals["is_closed"]]
    deal_by_lead = deals.set_index("lead_id")

    # ---- 1. duplicates (run first: the valuation constants exclude duplicate leads)
    dups = find_duplicates(leads, name_threshold)
    dup_ids = set(dups["duplicate_lead_id"])
    dup_deal_count = 0
    for r in dups.itertuples():
        usd, extra = 0.0, ""
        if r.duplicate_lead_id in deal_by_lead.index:
            d = deal_by_lead.loc[r.duplicate_lead_id]
            if not d["is_closed"]:
                usd = float(d["amount_usd"])
                dup_deal_count += 1
                extra = f"; open deal {d['deal_id']} (${usd:,.0f}) double-counts pipeline"
        findings.append(Finding("duplicate_leads", r.duplicate_lead_id,
                                f"{r.match_type} duplicate of {r.original_lead_id}: {r.reason}{extra}",
                                usd))

    # ---- shared valuation constants, as defined in docs/SCHEMA.md
    won = deals[deals["is_won"]]
    avg_won = won["amount_usd"].mean()
    worked = leads[leads["first_response_at"].notna() & ~leads["lead_id"].isin(dup_ids)]
    value_per_worked_lead = won["amount_usd"].sum() / len(worked)

    # ---- 2. stale proposals
    stale = open_deals[(open_deals["stage"] == "proposal")
                       & ((as_of - open_deals["last_activity_date"]).dt.days >= STALE_DAYS)]
    for d in stale.itertuples():
        days = (as_of - d.last_activity_date).days
        findings.append(Finding("stalled_proposals", d.deal_id,
                                f"{d.owner_rep_id} proposal, no activity for {days} days "
                                f"(last {d.last_activity_date.date()})", float(d.amount_usd)))

    # ---- 3. missing required fields
    for lead in leads.itertuples():
        missing = [f for f in REQUIRED_FIELDS if pd.isna(getattr(lead, f))]
        if missing:
            usd = value_per_worked_lead if "email" in missing else 0.0
            note = "cannot be worked" if "email" in missing else "routing/reporting gap, valued at $0"
            findings.append(Finding("missing_required_fields", lead.lead_id,
                                    f"missing {', '.join(missing)} ({note})", usd))

    # ---- 4. never followed up (past the grace period)
    never = leads[leads["first_response_at"].isna()
                  & ((as_of - leads["created_at"].dt.normalize()).dt.days >= FOLLOW_UP_GRACE_DAYS)  # calendar days
                  & ~leads["lead_id"].isin(dup_ids)]
    for lead in never.itertuples():
        age = (as_of - lead.created_at.normalize()).days
        findings.append(Finding("never_followed_up", lead.lead_id,
                                f"{lead.channel} lead from {lead.source}, {age} days old, no first response",
                                value_per_worked_lead))

    # ---- 5. slow first response on inbound leads, and the win-rate gap
    inbound = leads[(leads["channel"] == "inbound") & leads["first_response_minutes"].notna()]
    slow = inbound[inbound["first_response_minutes"] > SLOW_RESPONSE_MINUTES]
    fast = inbound[inbound["first_response_minutes"] <= SLOW_RESPONSE_MINUTES]
    closed = deals[deals["is_closed"]]
    slow_closed = closed[closed["lead_id"].isin(slow["lead_id"])]
    fast_closed = closed[closed["lead_id"].isin(fast["lead_id"])]
    slow_rate = slow_closed["is_won"].mean()
    fast_rate = fast_closed["is_won"].mean()
    slow_usd = max(0.0, len(slow_closed) * (fast_rate - slow_rate) * avg_won)
    per_slow = slow_usd / len(slow) if len(slow) else 0.0
    for lead in slow.itertuples():
        findings.append(Finding("slow_first_response", lead.lead_id,
                                f"inbound {lead.source}, first response after "
                                f"{int(lead.first_response_minutes)} min (SLA {SLOW_RESPONSE_MINUTES})",
                                per_slow))

    # ---- 6. rep-level proposal -> won outliers vs the rest of the team
    post = deals[deals["proposal_date"].notna() & deals["is_closed"]]
    by_rep = post.groupby("owner_rep_id")["is_won"].agg(won="sum", closed="count")
    rep_stats, weak_reps = {}, []
    for rep, row in by_rep.iterrows():
        rest = by_rep.drop(rep)
        p1, n1 = row["won"] / row["closed"], row["closed"]
        p2, n2 = rest["won"].sum() / rest["closed"].sum(), rest["closed"].sum()
        pooled = (row["won"] + rest["won"].sum()) / (n1 + n2)
        se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        z = (p1 - p2) / se if se else 0.0
        rep_stats[rep] = {"won": int(row["won"]), "closed": int(n1), "rate": round(p1, 4),
                          "rest_rate": round(p2, 4), "z": round(z, 2)}
        if z <= REP_OUTLIER_Z:
            weak_reps.append(rep)
    weak_usd = 0.0
    for rep in weak_reps:
        s = rep_stats[rep]
        rest = by_rep.drop(rep)
        rep_usd = s["closed"] * (rest["won"].sum() / rest["closed"].sum() - s["won"] / s["closed"]) * avg_won
        weak_usd += rep_usd
        rep_deals = post[post["owner_rep_id"] == rep]
        for d in rep_deals.itertuples():
            outcome = "won" if d.is_won else "lost"
            findings.append(Finding("weak_rep_proposal_conversion", d.deal_id,
                                    f"{rep} {outcome} after proposal; rep wins {s['rate']:.0%} vs "
                                    f"team {s['rest_rate']:.0%} (z={s['z']})",
                                    rep_usd / len(rep_deals)))

    context = {
        "as_of": AS_OF.isoformat(),
        "average_won_amount_usd": round(avg_won, 2),
        "value_per_worked_lead_usd": round(value_per_worked_lead, 2),
        "duplicates": {"exact": int((dups["match_type"] == "exact").sum()),
                       "fuzzy": int((dups["match_type"] == "fuzzy").sum()),
                       "duplicate_open_deals": dup_deal_count,
                       "name_similarity_threshold": name_threshold},
        "slow_first_response": {"sla_minutes": SLOW_RESPONSE_MINUTES,
                                "slow_closed_deals": len(slow_closed), "slow_won": int(slow_closed["is_won"].sum()),
                                "slow_win_rate": round(slow_rate, 4),
                                "fast_closed_deals": len(fast_closed), "fast_won": int(fast_closed["is_won"].sum()),
                                "fast_win_rate": round(fast_rate, 4)},
        "rep_proposal_to_won": {"z_threshold": REP_OUTLIER_Z, "flagged": weak_reps, "by_rep": rep_stats},
    }
    return findings, context


CHECK_ORDER = ["stalled_proposals", "slow_first_response", "duplicate_leads",
               "missing_required_fields", "never_followed_up", "weak_rep_proposal_conversion"]


def summarise(findings: list[Finding], context: dict) -> dict:
    checks = {}
    for name in CHECK_ORDER:
        rows = [f for f in findings if f.check == name]
        checks[name] = {"count": len(rows), "usd": round(sum(f.usd_at_risk for f in rows), 2)}
    return {"checks": checks,
            "total_usd": round(sum(c["usd"] for c in checks.values()), 2),
            "total_records": sum(c["count"] for c in checks.values()),
            "context": context}


def main() -> None:
    leads, deals = load()
    findings, context = run(leads, deals)
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([f.__dict__ for f in findings])
    df["usd_at_risk"] = df["usd_at_risk"].round(2)
    df.to_csv(OUT / "findings.csv", index=False, lineterminator="\n")
    summary = summarise(findings, context)
    with open(OUT / "summary.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    print(f"Hygiene check as of {AS_OF}: {summary['total_records']} flagged records")
    for name, c in summary["checks"].items():
        print(f"  {name:<30} {c['count']:>5}  ${c['usd']:>12,.2f}")
    print(f"  {'TOTAL':<30} {'':>5}  ${summary['total_usd']:>12,.2f}")
    print(f"Wrote {OUT.relative_to(ROOT) / 'findings.csv'} and {OUT.relative_to(ROOT) / 'summary.json'}")


if __name__ == "__main__":
    main()
