"""Speed-to-lead router.

A new inbound lead runs through six stages, each timed:

    validate -> dedupe -> enrich -> score -> assign -> alert

Run ``python router/route.py`` from the repo root to route one example lead and print the route.
``router/demo.py`` replays recent inbound leads from ``data/sample/leads.csv`` through the same
router.

Everything here is local and deterministic. Enrichment is a stub (see ``enrich``), the fit score is
a placeholder for the ``scoring/`` module, and the only network call is the optional Slack
webhook in ``send_alert``.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "sample"

STAGES = ("validate", "dedupe", "enrich", "score", "assign", "alert")

# Fields a web form must supply. Email is the only one we cannot work without, so a missing or
# malformed email rejects the lead; anything else is flagged and routing carries on.
REQUIRED_FIELDS = ("company", "contact_name", "email", "region", "source")
REJECT_IF_MISSING = ("email",)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")

# Mail providers anyone can sign up to. The domain says nothing about the company, so we never
# enrich from it and never treat a shared domain as a match.
PUBLIC_DOMAINS = frozenset(
    {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "proton.me"}
)

LEGAL_SUFFIXES = ("llc", "inc", "co", "ltd", "corp", "company", "limited", "gmbh", "plc")

# Open-lead capacity per rep: leads in status `new` or `contacted` that the rep still has to work.
DEFAULT_CAPACITY = 160
OPEN_STATUSES = frozenset({"new", "contacted"})

COMPANY_MATCH_CUTOFF = 0.85  # difflib ratio on normalised company names, plus a second signal


# --------------------------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rep:
    rep_id: str
    rep_name: str
    region: str
    email: str


@dataclass
class RouteResult:
    lead_id: str
    status: str = "routed"  # routed | rejected
    assigned_rep: str = ""
    assign_reason: str = ""
    score: int | None = None
    dedupe_hit: bool = False
    dedupe_match: str = ""  # e.g. "email:L00123" or "company:L00456"
    flags: list[str] = field(default_factory=list)
    enriched_fields: dict[str, str] = field(default_factory=dict)
    stage_ms: dict[str, float] = field(default_factory=dict)
    alert_text: str = ""

    @property
    def total_ms(self) -> float:
        return sum(self.stage_ms.values())


# --------------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------------


def load_reps(path: Path = DATA_DIR / "reps.csv") -> list[Rep]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Rep(r["rep_id"], r["rep_name"], r["region"], r["email"]) for r in csv.DictReader(f)]


def load_leads(path: Path = DATA_DIR / "leads.csv") -> list[dict]:
    """Leads as dicts of strings; empty CSV cells become ``None``."""
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]


def load_env() -> None:
    """Read ``.env`` at the repo root if python-dotenv is installed. Never prints values."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


# --------------------------------------------------------------------------------------------
# 1. validate
# --------------------------------------------------------------------------------------------


def validate(lead: dict) -> tuple[bool, list[str]]:
    """Return (accepted, flags). Rejects on a missing or malformed email; flags anything else."""
    flags = []
    for f in REQUIRED_FIELDS:
        if not (lead.get(f) or "").strip():
            flags.append(f"missing_{f}")
    email = (lead.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        flags.append("invalid_email")
    rejected = "invalid_email" in flags or any(f"missing_{f}" in flags for f in REJECT_IF_MISSING)
    return (not rejected), flags


# --------------------------------------------------------------------------------------------
# 2. dedupe
# --------------------------------------------------------------------------------------------


def email_domain(email: str | None) -> str:
    return email.rsplit("@", 1)[-1].lower() if email and "@" in email else ""


def business_domain(email: str | None) -> str:
    """Email domain, or '' for public mail providers (they say nothing about the company)."""
    domain = email_domain(email)
    return "" if domain in PUBLIC_DOMAINS else domain


def normalise_company(name: str | None) -> str:
    """'Larkwell Bakery, Inc.' and 'Lark Well Bakery' both become 'larkwellbakery'."""
    if not name:
        return ""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def surname(name: str | None) -> str:
    """Last word of a contact name, lower-cased: 'N. Galloway' and 'Nora Galloway' -> 'galloway'."""
    words = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
    return words[-1] if words else ""


class LeadIndex:
    """Existing leads, indexed for the two dedupe checks.

    * exact email (case-insensitive): the same person enquiring again
    * company name: a known account, so the lead goes to the account's owner. The normalised
      name either matches exactly, or matches fuzzily *and* a second signal agrees (same contact
      surname, or the same business email domain). Name similarity alone is not enough: invented
      names like 'Riverlight Clinic' and 'Silverlight Clinic' score above 0.9 but are different
      businesses.

    A shared public mail domain (gmail.com etc.) never counts as a signal.
    """

    def __init__(self, leads: list[dict] | None = None):
        self._by_email: dict[str, tuple[str, str]] = {}  # email -> (lead_id, owner)
        # normalised company -> [(lead_id, owner, surname, business_domain), ...] oldest first
        self._by_company: dict[str, list[tuple[str, str, str, str]]] = {}
        for lead in leads or []:
            self.add(lead, lead.get("owner_rep_id") or "")

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_company.values())

    def add(self, lead: dict, owner: str) -> None:
        """Index a lead. The oldest lead per email wins, mirroring 'original = lower id'."""
        email = (lead.get("email") or "").strip().lower()
        if email:
            self._by_email.setdefault(email, (lead["lead_id"], owner))
        company = normalise_company(lead.get("company"))
        if company:
            self._by_company.setdefault(company, []).append(
                (lead["lead_id"], owner, surname(lead.get("contact_name")), business_domain(email))
            )

    def find(self, lead: dict) -> tuple[str, str, str] | None:
        """Return (match_type, existing_lead_id, existing_owner) or None."""
        email = (lead.get("email") or "").strip().lower()
        if email and email in self._by_email:
            return ("email", *self._by_email[email])
        company = normalise_company(lead.get("company"))
        if not company:
            return None
        if company in self._by_company:
            lead_id, owner, _, _ = self._by_company[company][0]
            return ("company", lead_id, owner)
        name, domain = surname(lead.get("contact_name")), business_domain(email)
        for key in difflib.get_close_matches(company, self._by_company, n=3,
                                             cutoff=COMPANY_MATCH_CUTOFF):
            for lead_id, owner, other_name, other_domain in self._by_company[key]:
                if (name and name == other_name) or (domain and domain == other_domain):
                    return ("company", lead_id, owner)
        return None


# --------------------------------------------------------------------------------------------
# 3. enrich (STUB)
# --------------------------------------------------------------------------------------------

# STUB: a real router calls an enrichment API here (Clearbit / Apollo / ZoomInfo style) with the
# email domain and gets back firmographics. This stand-in guesses industry from words in the
# domain and derives a company-size band from a hash of the domain, so it is deterministic and
# free, but the size band is not real information.
INDUSTRY_KEYWORDS = {
    "Retail": ("boutique", "market", "supply", "goods", "outfitters", "store", "shop"),
    "Food & Beverage": ("bistro", "bakery", "coffee", "catering", "kitchen", "cafe", "deli"),
    "Professional Services": ("consulting", "accounting", "design", "legal", "advisors", "law"),
    "Health & Wellness": ("clinic", "dental", "physio", "pharmacy", "wellness", "chiro", "health"),
    "Fitness": ("gym", "athletics", "yoga", "fitness", "pilates", "crossfit"),
    "Beauty & Personal Care": ("salon", "spa", "barbers", "beauty", "nails"),
    "Real Estate": ("homes", "realty", "properties", "estates"),
    "Education": ("learning", "academy", "tutoring", "school"),
    "Automotive": ("auto", "motors", "tires", "garage"),
    "Hospitality": ("inn", "lodge", "guesthouse", "hotel", "suites"),
}
SIZE_BANDS = ("1-10", "11-50", "51-200")


def enrich(lead: dict) -> tuple[dict, dict[str, str], list[str]]:
    """Fill missing ``industry`` / ``company_size`` from the email domain.

    Returns (enriched_lead, fields_filled, flags). Values the form supplied are never overwritten.
    """
    out = dict(lead)
    filled: dict[str, str] = {}
    domain = email_domain(lead.get("email"))
    if not domain:
        return out, filled, []
    if domain in PUBLIC_DOMAINS:
        return out, filled, ["public_email_domain"]

    stem = domain.split(".")[0]
    if not out.get("industry"):
        # Longest keyword wins so 'guesthouse' beats 'house'-like overlaps.
        hits = [(len(k), ind) for ind, kws in INDUSTRY_KEYWORDS.items() for k in kws if k in stem]
        if hits:
            out["industry"] = filled["industry"] = max(hits)[1]
    if not out.get("company_size"):
        bucket = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(SIZE_BANDS)
        out["company_size"] = filled["company_size"] = SIZE_BANDS[bucket]
    flags = [] if "industry" in filled or out.get("industry") else ["enrich_no_industry"]
    return out, filled, flags


# --------------------------------------------------------------------------------------------
# 4. score (placeholder for scoring/)
# --------------------------------------------------------------------------------------------

# In production the router would call the scoring module (scoring/). This is a small, readable
# stand-in so the router has no dependency on work in progress elsewhere in the repo.
SOURCE_POINTS = {"demo_request": 35, "free_trial": 30, "website_form": 20, "content_download": 10}
SIZE_POINTS = {"51-200": 30, "11-50": 25, "1-10": 10}
CORE_INDUSTRIES = frozenset(
    {"Health & Wellness", "Fitness", "Beauty & Personal Care", "Professional Services"}
)


def fit_score(lead: dict) -> int:
    """0-100: buying intent (source) + company size + industry fit + business email."""
    score = SOURCE_POINTS.get(lead.get("source") or "", 0)
    score += SIZE_POINTS.get(lead.get("company_size") or "", 0)
    industry = lead.get("industry")
    score += 20 if industry in CORE_INDUSTRIES else 10 if industry else 0
    domain = email_domain(lead.get("email"))
    score += 15 if domain and domain not in PUBLIC_DOMAINS else 0
    return min(score, 100)


# --------------------------------------------------------------------------------------------
# 5. assign
# --------------------------------------------------------------------------------------------


class Assigner:
    """Round-robin within a region, skipping reps at capacity.

    ``load`` is each rep's open-lead count. If every rep in the region is full, the lead goes to the
    least-loaded rep in the region and is flagged ``over_capacity`` rather than sitting unowned.
    A lead with no known region uses the same rule across all reps.
    """

    def __init__(self, reps: list[Rep], load: dict[str, int] | None = None,
                 capacity: int = DEFAULT_CAPACITY):
        self.reps = {r.rep_id: r for r in reps}
        self.capacity = capacity
        self.load = {r.rep_id: 0 for r in reps}
        self.load.update(load or {})
        self._by_region: dict[str, list[str]] = {}
        for r in sorted(reps, key=lambda r: r.rep_id):
            self._by_region.setdefault(r.region, []).append(r.rep_id)
        self._next: dict[str, int] = {}  # region -> index of the next rep in the rotation

    def assign(self, region: str | None) -> tuple[str, str, list[str]]:
        """Return (rep_id, reason, flags) and count the lead against that rep."""
        flags = []
        if region in self._by_region:
            pool_key, pool = region, self._by_region[region]
        else:
            flags.append("unknown_region")
            pool_key, pool = "*", sorted(self.reps)

        start = self._next.get(pool_key, 0)
        for step in range(len(pool)):
            i = (start + step) % len(pool)
            rep_id = pool[i]
            if self.load[rep_id] < self.capacity:
                self._next[pool_key] = (i + 1) % len(pool)
                skipped = step
                reason = "round_robin" + (f" (skipped {skipped} at capacity)" if skipped else "")
                return self._take(rep_id), reason, flags

        rep_id = min(pool, key=lambda r: (self.load[r], r))
        flags.append("over_capacity")
        return self._take(rep_id), "least_loaded (region at capacity)", flags

    def take_owner(self, rep_id: str) -> str:
        """Count a lead routed to a fixed owner (dedupe hit) against that rep's load."""
        return self._take(rep_id)

    def _take(self, rep_id: str) -> str:
        self.load[rep_id] = self.load.get(rep_id, 0) + 1
        return rep_id


def open_load(leads: list[dict]) -> dict[str, int]:
    load: dict[str, int] = {}
    for lead in leads:
        if lead.get("status") in OPEN_STATUSES and lead.get("owner_rep_id"):
            load[lead["owner_rep_id"]] = load.get(lead["owner_rep_id"], 0) + 1
    return load


# --------------------------------------------------------------------------------------------
# 6. alert
# --------------------------------------------------------------------------------------------


def build_alert(lead: dict, result: RouteResult, rep: Rep | None) -> str:
    who = f"{rep.rep_name} ({rep.rep_id})" if rep else result.assigned_rep or "nobody"
    lines = [
        f":rotating_light: New inbound lead for {who}",
        f"*{lead.get('contact_name') or '?'}* at *{lead.get('company') or '?'}*"
        f" ({lead.get('region') or 'region unknown'})",
        f"Email: {lead.get('email')} | Source: {lead.get('source')} | Fit score: {result.score}",
        f"Industry: {lead.get('industry') or '?'} | Size: {lead.get('company_size') or '?'}",
        f"Why this rep: {result.assign_reason}",
    ]
    if result.dedupe_hit:
        lines.append(f"Known contact/account: {result.dedupe_match}")
    if result.flags:
        lines.append(f"Flags: {', '.join(result.flags)}")
    lines.append("Reply now: inbound leads answered within the hour close far more often.")
    return "\n".join(lines)


def send_alert(text: str, mode: str = "auto") -> list[str]:
    """Deliver the alert. Returns flags.

    mode ``auto``: POST to ``SLACK_WEBHOOK_URL`` if set, else print. ``print``: always print.
    ``silent``: build only (used by the demo replay so it doesn't post 50 messages).
    The webhook URL is a secret and is never printed or logged, including in error messages.
    """
    if mode == "silent":
        return []
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip() if mode == "auto" else ""
    if not url:
        print("\n--- alert (no SLACK_WEBHOOK_URL set, printing instead) ---")
        print(text)
        print("---")
        return ["alert_printed"]
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):
            return ["alert_slack"]
    except urllib.error.HTTPError as e:
        flag = f"alert_failed_http_{e.code}"
    except (urllib.error.URLError, OSError) as e:
        flag = f"alert_failed_{type(e).__name__}"
    print(f"\nSlack post failed ({flag}); printing the alert instead.")
    print(text)
    return [flag, "alert_printed"]


# --------------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------------


class Router:
    def __init__(self, reps: list[Rep], existing_leads: list[dict],
                 capacity: int = DEFAULT_CAPACITY, alert_mode: str = "auto"):
        self.reps = {r.rep_id: r for r in reps}
        self.index = LeadIndex(existing_leads)
        self.assigner = Assigner(reps, open_load(existing_leads), capacity)
        self.alert_mode = alert_mode

    def route(self, lead: dict) -> RouteResult:
        res = RouteResult(lead_id=lead.get("lead_id") or "")
        clock = time.perf_counter

        t = clock()
        ok, flags = validate(lead)
        res.flags += flags
        res.stage_ms["validate"] = (clock() - t) * 1000
        if not ok:
            res.status = "rejected"
            return res

        t = clock()
        match = self.index.find(lead)
        if match:
            kind, existing_id, owner = match
            res.dedupe_hit = True
            res.dedupe_match = f"{kind}:{existing_id}"
            res.flags.append("duplicate_contact" if kind == "email" else "known_account")
        res.stage_ms["dedupe"] = (clock() - t) * 1000

        t = clock()
        lead, res.enriched_fields, flags = enrich(lead)
        res.flags += flags
        res.stage_ms["enrich"] = (clock() - t) * 1000

        t = clock()
        res.score = fit_score(lead)
        res.stage_ms["score"] = (clock() - t) * 1000

        t = clock()
        if match and match[2] in self.reps:
            # Keep the person/account with the rep who already owns it.
            res.assigned_rep = self.assigner.take_owner(match[2])
            res.assign_reason = f"existing owner ({match[0]} match)"
        else:
            res.assigned_rep, res.assign_reason, flags = self.assigner.assign(lead.get("region"))
            res.flags += flags
        self.index.add(lead, res.assigned_rep)
        res.stage_ms["assign"] = (clock() - t) * 1000

        t = clock()
        res.alert_text = build_alert(lead, res, self.reps.get(res.assigned_rep))
        res.flags += send_alert(res.alert_text, self.alert_mode)
        res.stage_ms["alert"] = (clock() - t) * 1000
        return res


def format_route(res: RouteResult) -> str:
    path = " -> ".join(f"{s} {res.stage_ms[s]:.2f} ms" for s in STAGES if s in res.stage_ms)
    head = f"{res.lead_id or '(new lead)'}: {path}"
    if res.status == "rejected":
        return f"{head}\nRejected at validate ({', '.join(res.flags)})"
    return (f"{head}\nOwner: {res.assigned_rep} [{res.assign_reason}] | score {res.score}"
            f"{' | ' + res.dedupe_match if res.dedupe_hit else ''}"
            f"{' | flags: ' + ', '.join(res.flags) if res.flags else ''}\n"
            f"Lead to owner in {res.total_ms:.2f} ms")


EXAMPLE_LEAD = {
    "lead_id": "NEW-0001",
    "company": "Quillbrook Pilates Studio",
    "contact_name": "Avery Example",
    "email": "avery@quillbrookpilates.example",
    "region": "EMEA",
    "source": "demo_request",
    "created_at": "2026-10-31 09:00:00",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route one inbound lead and print the route.")
    ap.add_argument("--lead-json", help="Lead as a JSON object (defaults to a built-in example)")
    ap.add_argument("--print-alert", action="store_true",
                    help="Print the alert even if SLACK_WEBHOOK_URL is set")
    args = ap.parse_args(argv)

    load_env()
    lead = json.loads(args.lead_json) if args.lead_json else EXAMPLE_LEAD
    router = Router(load_reps(), load_leads(), alert_mode="print" if args.print_alert else "auto")
    print(format_route(router.route(lead)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
