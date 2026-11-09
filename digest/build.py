"""Weekly AI digest: pipeline facts -> Claude -> number-checked Monday brief.

Three stages, in this order:

1. ``compute_facts``  -- pandas only. Every number the digest may use is computed here and
   saved to ``digest/output/facts.json``.
2. ``generate``       -- sends facts.json (and nothing else) to Claude and asks for a short brief.
3. ``check_digest``   -- pulls every number out of the text and looks it up in facts.json.
   Any number that is not there fails the run. One retry with the error, then a hard failure.

Usage (from the repo root):
    python digest/build.py             # facts + Claude + check -> latest.md, digest-<as-of>.md
    python digest/build.py --dry-run   # facts.json and prompt.md only, no API call
    python digest/build.py --slack     # also post the brief to SLACK_WEBHOOK_URL from .env
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sample"
OUT = Path(__file__).resolve().parent / "output"

MODEL = "claude-sonnet-5-5"
PRICE_IN_PER_MTOK = 2.00   # USD, claude-sonnet-5-5 input
PRICE_OUT_PER_MTOK = 10.00  # USD, claude-sonnet-5-5 output (thinking tokens bill as output)
MAX_WORDS = 250
HYPE_WORDS = [
    "amazing", "incredible", "game-changer", "game changer", "skyrocket", "supercharge",
    "unlock", "unleash", "revolutionary", "massive", "huge", "crushing", "exciting",
    "leverage", "synergy", "robust", "seamless", "powerful", "transformative",
]

STALL_DAYS = 30          # SCHEMA leak 1
SLA_MINUTES = 60         # SCHEMA leak 2
UNTOUCHED_DAYS = 7       # SCHEMA leak 5: never-followed-up leads are at least 7 days old
WINDOW_DAYS = 7


# --------------------------------------------------------------------------------------------
# 1. Facts
# --------------------------------------------------------------------------------------------

def _read(path: Path, **kw) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, **kw)


def _in_window(ts: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    day = ts.dt.normalize()
    return (day >= start) & (day <= end)


def _change(metric: str, this: float, prior: float, lower_is_better: bool, unit: str) -> dict:
    delta = this - prior
    if delta == 0:
        direction = "flat"
    elif (delta < 0) == lower_is_better:
        direction = "better"
    else:
        direction = "worse"
    return {
        "metric": metric,
        "unit": unit,
        "this_week": this,
        "prior_week": prior,
        "change": delta,
        "direction": direction,
    }


def compute_facts() -> dict:
    leads = _read(DATA / "leads.csv")
    deals = _read(DATA / "deals.csv")
    reps = _read(DATA / "reps.csv")
    replies = _read(DATA / "replies.csv", usecols=["reply_id", "lead_id", "received_at"])
    labels = _read(ROOT / "classifier" / "output" / "labels.csv")
    scores = _read(ROOT / "scoring" / "output" / "scores.csv")
    findings = _read(ROOT / "hygiene" / "output" / "findings.csv")
    summary = json.loads((ROOT / "hygiene" / "output" / "summary.json").read_text("utf-8"))

    rep_name = dict(zip(reps.rep_id, reps.rep_name))

    # As-of = the latest date anywhere in the data (2026-10-31), never the real clock.
    stamps = pd.concat([
        pd.to_datetime(leads.created_at), pd.to_datetime(leads.last_activity_date, errors="coerce"),
        pd.to_datetime(deals.last_activity_date), pd.to_datetime(replies.received_at),
    ]).dropna()
    as_of = stamps.max().normalize()
    this_end, this_start = as_of, as_of - pd.Timedelta(days=WINDOW_DAYS - 1)
    prior_end, prior_start = this_start - pd.Timedelta(days=1), this_start - pd.Timedelta(days=WINDOW_DAYS)
    windows = {"this": (this_start, this_end), "prior": (prior_start, prior_end)}

    def per_week(fn) -> dict:
        return {k: fn(*w) for k, w in windows.items()}

    # --- Stalled proposals: open at proposal, no activity for 30+ days ---------------------
    prop = deals[deals.stage == "proposal"].copy()
    prop["amount"] = prop.amount_usd.astype(int)
    prop["last"] = pd.to_datetime(prop.last_activity_date)
    prop["idle_days"] = (as_of - prop["last"]).dt.days
    stalled = prop[prop.idle_days >= STALL_DAYS]
    crossed_on = stalled["last"] + pd.Timedelta(days=STALL_DAYS)  # the day it became a leak
    crossed = per_week(lambda s, e: stalled[_in_window(crossed_on, s, e)])

    stalled_by_rep = (
        stalled.groupby("owner_rep_id").agg(count=("deal_id", "size"), usd=("amount", "sum"))
        .reset_index().sort_values(["usd", "owner_rep_id"], ascending=[False, True])
    )
    stalled_by_rep_list = [
        {"rep_id": r.owner_rep_id, "rep_name": rep_name[r.owner_rep_id],
         "count": int(r["count"]), "usd": int(r.usd)}
        for _, r in stalled_by_rep.iterrows()
    ]
    top_stall_rep = stalled_by_rep_list[0]

    # --- Inbound speed-to-lead -------------------------------------------------------------
    created = pd.to_datetime(leads.created_at)
    inbound = leads.channel == "inbound"
    minutes = pd.to_numeric(leads.first_response_minutes, errors="coerce")

    def inbound_week(s, e):
        m = inbound & _in_window(created, s, e)
        return {
            "inbound_leads": int(m.sum()),
            "slow_over_60_min": int((m & (minutes > SLA_MINUTES)).sum()),
            "median_first_response_minutes": int(round(minutes[m].median())),
        }
    speed = per_week(inbound_week)

    # --- Duplicates and untouched leads (record lists come from the hygiene check) ---------
    dup_ids = set(findings.loc[findings.check == "duplicate_leads", "record_id"])
    nf_ids = set(findings.loc[findings.check == "never_followed_up", "record_id"])
    is_dup, is_nf = leads.lead_id.isin(dup_ids), leads.lead_id.isin(nf_ids)
    new_dups = per_week(lambda s, e: int((is_dup & _in_window(created, s, e)).sum()))
    # An untouched lead becomes a leak on day 7 with no first response.
    untouched_crossed = per_week(lambda s, e: int(
        (is_nf & _in_window(created + pd.Timedelta(days=UNTOUCHED_DAYS), s, e)).sum()))

    # --- Interested replies waiting: the lead's latest reply is "interested" (classifier
    # label, not the hidden true_label), the lead is still open (contacted / nurturing) and
    # has no deal yet.
    rep_lab = replies.merge(labels[["reply_id", "predicted_label"]], on="reply_id")
    rep_lab["ts"] = pd.to_datetime(rep_lab.received_at)
    latest = (rep_lab.sort_values(["ts", "reply_id"]).groupby("lead_id").tail(1)
              .merge(leads[["lead_id", "status", "owner_rep_id"]], on="lead_id"))
    waiting = latest[(latest.predicted_label == "interested")
                     & latest.status.isin(["contacted", "nurturing"])].copy()
    waiting["age_days"] = (as_of - waiting.ts.dt.normalize()).dt.days
    waiting_new = per_week(lambda s, e: int(_in_window(waiting.ts, s, e).sum()))
    waiting_by_rep = (waiting.groupby("owner_rep_id").size().sort_values(ascending=False))
    top_wait_rep = waiting_by_rep.index[0]

    # --- A-tier leads --------------------------------------------------------------------
    a_tier = scores[scores.tier == "A"].merge(leads, on="lead_id")
    a_not_contacted = int((a_tier.first_response_at == "").sum())
    a_waiting = int(waiting.lead_id.isin(a_tier.lead_id).sum())
    a_new = per_week(lambda s, e: int(_in_window(pd.to_datetime(a_tier.created_at), s, e).sum()))

    # --- Closed business -----------------------------------------------------------------
    closed = pd.to_datetime(deals.closed_date, errors="coerce")
    won = deals.is_won == "True"
    won_wk = per_week(lambda s, e: {
        "count": int((won & _in_window(closed, s, e)).sum()),
        "usd": int(deals.loc[won & _in_window(closed, s, e), "amount_usd"].astype(int).sum()),
    })
    new_deals = per_week(lambda s, e: int(_in_window(pd.to_datetime(deals.qualified_date), s, e).sum()))

    # --- Rep outlier (hygiene check: proposal-to-won z-score) -----------------------------
    rep_ctx = summary["context"]["rep_proposal_to_won"]
    outlier_id = rep_ctx["flagged"][0]
    o = rep_ctx["by_rep"][outlier_id]
    best_id = max(rep_ctx["by_rep"], key=lambda r: rep_ctx["by_rep"][r]["rate"])
    checks = summary["checks"]
    outlier = {
        "rep_id": outlier_id,
        "rep_name": rep_name[outlier_id],
        "proposal_to_won_pct": round(o["rate"] * 100),
        "other_reps_pct": round(o["rest_rate"] * 100),
        "won": o["won"],
        "closed_after_proposal": o["closed"],
        "z_score": o["z"],
        "estimated_usd_lost": round(checks["weak_rep_proposal_conversion"]["usd"]),
        "best_rep_id": best_id,
        "best_rep_name": rep_name[best_id],
        "best_rep_pct": round(rep_ctx["by_rep"][best_id]["rate"] * 100),
        "stalled_proposals_owned": next(
            (r["count"] for r in stalled_by_rep_list if r["rep_id"] == outlier_id), 0),
    }

    # --- All six leaks (whole book, from the hygiene check) -------------------------------
    leak_labels = {
        "stalled_proposals": "Deals stalled at proposal 30+ days",
        "weak_rep_proposal_conversion": f"Rep {outlier_id} proposal-to-won gap",
        "slow_first_response": "Inbound leads answered after 60 minutes",
        "duplicate_leads": "Duplicate leads (pipeline counted twice)",
        "never_followed_up": "Inbound leads never followed up",
        "missing_required_fields": "Leads missing a required field",
    }
    leaks = sorted(
        ({"leak": leak_labels[k], "count": v["count"], "usd": round(v["usd"])} for k, v in checks.items()),
        key=lambda x: -x["usd"],
    )

    # --- Candidate actions, ranked by $ in Python (the model picks from this list) --------
    actions = [
        {"action": f"Call every stalled proposal this week, oldest first ({len(stalled)} deals)",
         "usd": int(stalled.amount.sum()),
         "owner": f"Each deal's rep; largest book is {top_stall_rep['rep_name']} "
                  f"({top_stall_rep['rep_id']}, {top_stall_rep['count']} deals, ${top_stall_rep['usd']:,})"},
        {"action": f"Coach {outlier_id} on proposal follow-through; review open proposals with them",
         "usd": outlier["estimated_usd_lost"],
         "owner": f"Sales manager, with {outlier['best_rep_name']} ({best_id}) as peer coach"},
        {"action": "Enforce a 60-minute first-response SLA on inbound leads with an alert",
         "usd": round(checks["slow_first_response"]["usd"]),
         "owner": "RevOps (router alert) and the inbound reps"},
        {"action": "Merge duplicate leads and close the second open deal on each",
         "usd": round(checks["duplicate_leads"]["usd"]),
         "owner": "RevOps"},
        {"action": "Work the never-followed-up inbound leads today",
         "usd": round(checks["never_followed_up"]["usd"]),
         "owner": "Lead owners; RevOps to reassign any left after 24 hours"},
        {"action": f"Reply to the {len(waiting)} leads whose last reply said they were interested",
         "usd": None,
         "owner": f"Lead owners; most are with {rep_name[top_wait_rep]} ({top_wait_rep}, "
                  f"{int(waiting_by_rep.iloc[0])} leads)"},
    ]
    priced = sorted([a for a in actions if a["usd"] is not None], key=lambda a: -a["usd"])
    for i, a in enumerate(priced, 1):
        a["rank_by_usd"] = i
    unpriced = [a for a in actions if a["usd"] is None]

    # --- Week over week ------------------------------------------------------------------
    t, p = "this", "prior"
    changes = [
        _change("Proposals that crossed 30 days idle", len(crossed[t]), len(crossed[p]), True, "deals"),
        _change("USD in proposals that crossed 30 days idle", int(crossed[t].amount.sum()),
                int(crossed[p].amount.sum()), True, "USD"),
        _change("Inbound leads answered after 60 minutes", speed[t]["slow_over_60_min"],
                speed[p]["slow_over_60_min"], True, "leads"),
        _change("Median inbound first response", speed[t]["median_first_response_minutes"],
                speed[p]["median_first_response_minutes"], True, "minutes"),
        _change("New duplicate leads created", new_dups[t], new_dups[p], True, "leads"),
        _change("Inbound leads that hit 7 days untouched", untouched_crossed[t], untouched_crossed[p], True, "leads"),
        _change("New interested replies still waiting", waiting_new[t], waiting_new[p], True, "replies"),
        _change("Deals won", won_wk[t]["count"], won_wk[p]["count"], False, "deals"),
        _change("USD won", won_wk[t]["usd"], won_wk[p]["usd"], False, "USD"),
        _change("New deals opened", new_deals[t], new_deals[p], False, "deals"),
    ]

    d = lambda ts: ts.strftime("%Y-%m-%d")  # noqa: E731
    return {
        "as_of": d(as_of),
        "this_week": {"start": d(this_start), "end": d(this_end)},
        "prior_week": {"start": d(prior_start), "end": d(prior_end)},
        "rules": {"stall_days": STALL_DAYS, "response_sla_minutes": SLA_MINUTES,
                  "untouched_days": UNTOUCHED_DAYS, "window_days": WINDOW_DAYS},
        "money_at_risk": {
            "total_usd": round(summary["total_usd"]),
            "records": summary["total_records"],
            "by_leak": leaks,
            "new_this_week_usd": int(crossed[t].amount.sum()),
            "new_prior_week_usd": int(crossed[p].amount.sum()),
            "note": "new_*_week_usd counts proposals that crossed 30 days idle in that week",
        },
        "new_leaks_this_week": {
            "stalled_proposals_crossed_30_days": {"count": len(crossed[t]), "usd": int(crossed[t].amount.sum())},
            "slow_inbound_responses": {"count": speed[t]["slow_over_60_min"],
                                       "of_inbound_leads": speed[t]["inbound_leads"]},
            "new_duplicate_leads": new_dups[t],
            "leads_untouched_7_days": untouched_crossed[t],
        },
        "stalled_proposals": {
            "open_count": len(stalled),
            "open_usd": int(stalled.amount.sum()),
            "oldest_idle_days": int(stalled.idle_days.max()),
            "by_rep": stalled_by_rep_list,
        },
        "interested_replies_waiting": {
            "count": len(waiting),
            "received_this_week": waiting_new[t],
            "waiting_over_7_days": int((waiting.age_days > WINDOW_DAYS).sum()),
            "top_rep_id": top_wait_rep,
            "top_rep_name": rep_name[top_wait_rep],
            "top_rep_count": int(waiting_by_rep.iloc[0]),
        },
        "a_tier_leads": {
            "total": len(a_tier),
            "created_this_week": a_new[t],
            "not_contacted": a_not_contacted,
            "with_interested_reply_waiting": a_waiting,
        },
        "rep_outlier": outlier,
        "week_over_week": changes,
        "actions_ranked_by_usd": priced,
        "actions_without_usd": unpriced,
    }


# --------------------------------------------------------------------------------------------
# 2. Prompt and generation
# --------------------------------------------------------------------------------------------

SYSTEM = f"""You write a short Monday pipeline brief for a sales leader at a small B2B SaaS company.

Your only source is the JSON facts in the user message. Rules:
- Every number you write must appear in the JSON. You may add a $ sign, thousands separators
  or a % sign, and you may round USD to the nearest thousand (e.g. $454k). Never add, subtract,
  divide or compare numbers yourself. If the JSON has no number for something, say it in words.
- Write numbers as digits, dates as YYYY-MM-DD, and reps as name plus ID, e.g. "Jane Doe (R02)".
- Plain, direct sentences. No hype words, no exclamation marks, no emoji.
- Under {MAX_WORDS} words in total.

Use exactly this Markdown layout, with no title line:

**Summary**
Three lines, each its own sentence on its own line.

**Top 3 actions (by $ at risk)**
1. Action, $ amount, Owner: who.
2. ...
3. ...
Take these from actions_ranked_by_usd, in that order.

**Better / worse vs last week**
- Two to four bullets from week_over_week, each saying better or worse.

**Risk**
One sentence."""


def build_user_prompt(facts: dict, feedback: str | None = None, previous: str | None = None) -> str:
    msg = ("Facts for the week ending " + facts["as_of"] + ":\n\n```json\n"
           + json.dumps(facts, indent=2) + "\n```\n\nWrite the brief.")
    if feedback:
        msg += ("\n\nYour previous draft failed the automatic check:\n" + feedback
                + "\n\nPrevious draft:\n" + (previous or "") +
                "\n\nRewrite it. Use only numbers that appear in the JSON.")
    return msg


def generate(facts: dict, feedback: str | None = None, previous: str | None = None) -> tuple[str, dict]:
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (expected in .env). Use --dry-run to skip the API.")
    client = anthropic.Anthropic()  # reads the key from the environment; it is never printed

    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if Sonnet declines, the API re-runs on a fallback model
        messages=[{"role": "user", "content": build_user_prompt(facts, feedback, previous)}],
    )
    if resp.stop_reason == "refusal":
        sys.exit(f"Claude declined to write the digest (request {resp._request_id}).")
    if resp.stop_reason == "max_tokens":
        sys.exit("Digest was cut off at max_tokens.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    usage = {
        "model": resp.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "request_id": resp._request_id,
    }
    return text, usage


# --------------------------------------------------------------------------------------------
# 3. The number check
# --------------------------------------------------------------------------------------------

# A number: optional $, digits with optional thousands commas, optional decimals, optional
# k / M / % suffix. Not preceded by a letter or digit, so IDs like R02 or D0589 are skipped.
NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(\$)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s?(%|k|K|M)(?![A-Za-z0-9]))?"
)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LIST_MARKER_RE = re.compile(r"^\s*\d+\.\s", re.M)
WORD_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "thousand": 1000, "million": 1_000_000, "dozen": 12, "half": 0.5,
    "double": 2, "doubled": 2, "triple": 3, "tripled": 3, "twice": 2,
}
SCALE = {"k": 1_000, "K": 1_000, "M": 1_000_000}


def _tokens(text: str) -> list[dict]:
    """Every number in ``text`` as {raw, value, decimals, scale, pct}."""
    out = []
    for m in NUM_RE.finditer(text):
        dollar, whole, frac, suffix = m.groups()
        value = float(whole.replace(",", "") + (frac or ""))
        out.append({
            "raw": m.group(0).strip(),
            "value": value,
            "decimals": len(frac) - 1 if frac else 0,
            "scale": SCALE.get(suffix, 1),
            "pct": suffix == "%",
        })
    return out


def allowed_values(facts) -> set[float]:
    """Every number in facts.json: numeric leaves plus numbers written inside string leaves."""
    vals: set[float] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            vals.add(float(x))
        elif isinstance(x, str):
            for d in DATE_RE.findall(x):
                vals.update(float(p) for p in d.split("-"))
            for t in _tokens(DATE_RE.sub(" ", x)):
                vals.add(t["value"] * t["scale"])
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(facts)
    return vals


def _matches(tok: dict, allowed: set[float]) -> bool:
    """True if some fact value, formatted the way the token is, gives the token.

    Formatting allowed: $ and commas; rounding to the token's decimal places; a k/M scale
    (454200 -> $454k or $454.2k); a % sign on a fact already in percent, or on a 0-1 rate.
    A sign may be dropped ("down 3" for a change of -3).
    """
    d, scale = tok["decimals"], tok["scale"]
    for v in allowed:
        cands = [v, abs(v)]
        if tok["pct"] and abs(v) <= 1:
            cands.append(v * 100)
        for c in cands:
            if abs(round(c / scale, d) - tok["value"]) < 1e-9:
                return True
    return False


def check_digest(text: str, facts: dict) -> dict:
    allowed = allowed_values(facts)
    fact_dates = set(DATE_RE.findall(json.dumps(facts)))
    errors: list[str] = []

    body = LIST_MARKER_RE.sub("", text)  # "1." "2." "3." list markers are layout, not claims
    checked = 0
    for dt in DATE_RE.findall(body):
        checked += 1
        if dt not in fact_dates:
            errors.append(f"date {dt} is not in facts.json")
    body = DATE_RE.sub(" ", body)
    for tok in _tokens(body):
        checked += 1
        if not _matches(tok, allowed):
            errors.append(f"number '{tok['raw']}' is not in facts.json")
    for w in re.findall(r"[A-Za-z]+", body.lower()):
        if w in WORD_NUMBERS:
            checked += 1
            if WORD_NUMBERS[w] not in allowed:
                errors.append(f"number word '{w}' is not in facts.json")

    words = len(re.findall(r"\S+", text))
    if words > MAX_WORDS:
        errors.append(f"{words} words; the limit is {MAX_WORDS}")
    low = text.lower()
    for h in HYPE_WORDS:
        if re.search(r"\b" + re.escape(h) + r"\b", low):
            errors.append(f"hype word '{h}'")
    if "!" in text:
        errors.append("exclamation mark")
    return {"passed": not errors, "numbers_checked": checked, "words": words, "errors": errors}


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # LF endings on every OS, so reruns diff cleanly


def render(text: str, facts: dict, check: dict, model: str) -> str:
    return (
        f"# Pipeline brief: week ending {facts['as_of']}\n\n"
        f"_Week {facts['this_week']['start']} to {facts['this_week']['end']}, compared with "
        f"{facts['prior_week']['start']} to {facts['prior_week']['end']}._\n\n"
        f"{text}\n\n---\n"
        f"_Written by `{model}` from `digest/output/facts.json` only. "
        f"Number check: {check['numbers_checked']} numbers and dates in the text, "
        f"all found in facts.json._\n"
    )


def post_to_slack(markdown: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("--slack: SLACK_WEBHOOK_URL is not set in .env; skipped.")
        return
    # Slack mrkdwn uses *bold*, not **bold**.
    body = json.dumps({"text": markdown.replace("**", "*")}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:  # the URL itself is never printed
        print(f"--slack: posted (HTTP {r.status}).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="write facts.json and prompt.md; no API call")
    ap.add_argument("--slack", action="store_true", help="also post to SLACK_WEBHOOK_URL from .env")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    facts = compute_facts()
    _write(OUT / "facts.json", json.dumps(facts, indent=2) + "\n")
    _write(OUT / "prompt.md", "# System\n\n" + SYSTEM + "\n\n# User\n\n" + build_user_prompt(facts) + "\n")
    print(f"facts.json written (as of {facts['as_of']}, "
          f"{len(allowed_values(facts))} distinct numbers available to the model).")
    if args.dry_run:
        print("--dry-run: wrote facts.json and prompt.md; no API call.")
        return

    attempts, usages = [], []
    text, usage = generate(facts)
    usages.append(usage)
    check = check_digest(text, facts)
    attempts.append({"attempt": 1, **check})
    if not check["passed"]:
        print("Check failed on attempt 1, retrying once:\n  " + "\n  ".join(check["errors"]))
        text, usage = generate(facts, feedback="\n".join(check["errors"]), previous=text)
        usages.append(usage)
        check = check_digest(text, facts)
        attempts.append({"attempt": 2, **check})

    tokens_in = sum(u["input_tokens"] for u in usages)
    tokens_out = sum(u["output_tokens"] for u in usages)
    cost = tokens_in / 1e6 * PRICE_IN_PER_MTOK + tokens_out / 1e6 * PRICE_OUT_PER_MTOK
    run = {
        "as_of": facts["as_of"],
        "model_requested": MODEL,
        "calls": usages,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": round(cost, 4),
        "price_basis": f"${PRICE_IN_PER_MTOK}/M input, ${PRICE_OUT_PER_MTOK}/M output ({MODEL})",
        "attempts": attempts,
    }
    _write(OUT / "run.json", json.dumps(run, indent=2) + "\n")

    if not check["passed"]:
        _write(OUT / "rejected.md", text + "\n")
        sys.exit("NUMBER CHECK FAILED after one retry. Nothing published; draft saved to "
                 "digest/output/rejected.md.\n  " + "\n  ".join(check["errors"]))
    (OUT / "rejected.md").unlink(missing_ok=True)

    md = render(text, facts, check, usages[-1]["model"])
    _write(OUT / "latest.md", md)
    _write(OUT / f"digest-{facts['as_of']}.md", md)
    print(md)
    print(f"Check passed ({check['numbers_checked']} numbers, {check['words']} words, "
          f"{len(attempts)} attempt(s)). Cost ${cost:.4f} "
          f"({tokens_in} in / {tokens_out} out tokens).")
    if args.slack:
        post_to_slack(md)


if __name__ == "__main__":
    main()
