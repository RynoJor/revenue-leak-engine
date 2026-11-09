"""Replay the 50 most recent inbound leads through the router.

    python router/demo.py            # from the repo root
    python router/demo.py --alerts   # also send each alert (Slack if SLACK_WEBHOOK_URL is set)

Writes ``router/output/routing_log.csv`` (one row per lead) and ``router/output/routing_summary.json``
(the headline numbers), then compares routing time with the historical first response.

The replay pretends each lead is arriving now. The CRM it dedupes against is every lead created
before the first replayed lead, plus the replayed leads as they are routed. The web form is assumed
to collect company, contact name, email, region and source; industry and company size are left for
the enrich stage to fill, as they would be with a real form.

Timings are wall-clock on this machine, so they change slightly from run to run.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import route  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"
REPLAY_N = 50
FORM_FIELDS = ("lead_id", "company", "contact_name", "email", "region", "source", "created_at")
SLOW_MINUTES = 60  # the slow-first-response leak threshold in docs/SCHEMA.md

LOG_COLUMNS = [
    "lead_id", "assigned_rep", "score", "total_ms",
    *(f"{s}_ms" for s in route.STAGES),
    "dedupe_hit", "flags",
    # extra context for the dashboard
    "status", "region", "dedupe_match", "assign_reason",
    "historical_owner_rep_id", "historical_first_response_minutes",
]


def pick_replay(leads: list[dict], n: int = REPLAY_N) -> tuple[list[dict], list[dict]]:
    """Return (the n most recent inbound leads, oldest first; the CRM as it stood before them)."""
    inbound = sorted((l for l in leads if l["channel"] == "inbound"),
                     key=lambda l: (l["created_at"], l["lead_id"]))
    replay = inbound[-n:]
    cutoff = replay[0]["created_at"]
    existing = [l for l in leads if l["created_at"] < cutoff]
    return replay, existing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alerts", action="store_true",
                    help="Send each alert (Slack if SLACK_WEBHOOK_URL is set, else print)")
    ap.add_argument("-n", type=int, default=REPLAY_N, help="Number of leads to replay")
    args = ap.parse_args(argv)

    route.load_env()
    leads = route.load_leads()
    replay, existing = pick_replay(leads, args.n)
    router = route.Router(route.load_reps(), existing,
                          alert_mode="auto" if args.alerts else "silent")

    rows, results = [], []
    for lead in replay:
        res = router.route({k: lead.get(k) for k in FORM_FIELDS})
        results.append((lead, res))
        print(route.format_route(res).replace("\n", "\n    "))
        rows.append({
            "lead_id": res.lead_id,
            "assigned_rep": res.assigned_rep,
            "score": "" if res.score is None else res.score,
            "total_ms": round(res.total_ms, 3),
            **{f"{s}_ms": round(res.stage_ms[s], 3) if s in res.stage_ms else ""
               for s in route.STAGES},
            "dedupe_hit": res.dedupe_hit,
            "flags": ";".join(res.flags),
            "status": res.status,
            "region": lead.get("region") or "",
            "dedupe_match": res.dedupe_match,
            "assign_reason": res.assign_reason,
            "historical_owner_rep_id": lead.get("owner_rep_id") or "",
            "historical_first_response_minutes": lead.get("first_response_minutes") or "",
        })

    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / "routing_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    routed = [r for _, r in results if r.status == "routed"]
    ms = [r.total_ms for r in routed]
    hist = [int(l["first_response_minutes"]) for l, _ in results if l.get("first_response_minutes")]
    summary = {
        "leads_replayed": len(results),
        "routed": len(routed),
        "rejected": len(results) - len(routed),
        "dedupe_hits": sum(r.dedupe_hit for _, r in results),
        "over_capacity": sum("over_capacity" in r.flags for _, r in results),
        "routing_ms_median": round(statistics.median(ms), 2),
        "routing_ms_max": round(max(ms), 2),
        "historical_first_response_minutes_median": statistics.median(hist),
        "historical_responded": len(hist),
        "historical_over_60_min": sum(m > SLOW_MINUTES for m in hist),
        "historical_never_responded": len(results) - len(hist),
        "assigned_by_rep": {rep: sum(r.assigned_rep == rep for r in routed)
                            for rep in sorted(router.reps)},
    }
    with open(OUT_DIR / "routing_summary.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    s = summary
    print(f"\nReplayed {s['leads_replayed']} inbound leads: {s['routed']} routed, "
          f"{s['rejected']} rejected, {s['dedupe_hits']} matched an existing contact or account.")
    print(f"Historical median first response {s['historical_first_response_minutes_median']:g} "
          f"minutes ({s['historical_over_60_min']} of {s['historical_responded']} waited over an "
          f"hour, {s['historical_never_responded']} never got a reply); "
          f"routed in {s['routing_ms_median']:.1f} ms (median, max {s['routing_ms_max']:.1f} ms).")
    print("The router removes the wait for someone to pick an owner and tells that rep at once. "
          "A person still has to reply; the router does not do that part.")
    print(f"Wrote {OUT_DIR.relative_to(route.REPO_ROOT).as_posix()}/routing_log.csv "
          f"and routing_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
