"""Score hygiene/check.py against the planted answer key.

This is the only file in hygiene/ that reads data/sample/answer_key/. It re-runs nothing on the
key; it compares the findings check.py already wrote with the records the generator planted.

Run from the repo root (after python hygiene/check.py):
    python hygiene/evaluate.py

Writes hygiene/output/evaluation.json and prints the tables used in hygiene/README.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402  (same folder; used for the duplicate stress test)

ROOT = check.ROOT
KEY = ROOT / "data" / "sample" / "answer_key"
OUT = check.OUT


def prf(found: set, truth: set) -> dict:
    tp = len(found & truth)
    return {"found": len(found), "planted": len(truth), "true_pos": tp,
            "false_pos": len(found - truth), "missed": len(truth - found),
            "precision": tp / len(found) if found else 1.0,
            "recall": tp / len(truth) if truth else 1.0}


def main() -> None:
    findings = pd.read_csv(OUT / "findings.csv", dtype={"record_id": str})
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    planted = pd.read_csv(KEY / "planted_records.csv", dtype=str)
    pairs = pd.read_csv(KEY / "duplicate_pairs.csv", dtype=str)
    key = json.loads((KEY / "leak_summary.json").read_text(encoding="utf-8"))
    key_leaks = {l["id"]: l for l in key["leaks"]}
    deals = pd.read_csv(ROOT / "data" / "sample" / "deals.csv", dtype=str)

    results = {}
    for name in check.CHECK_ORDER:
        found = set(findings.loc[findings["check"] == name, "record_id"])
        if name == "weak_rep_proposal_conversion":
            # The key names the rep, not a record list. Truth = that rep's closed deals that
            # reached proposal, which is the population the leak is measured on.
            rep = key_leaks[name]["detail"]["rep_id"]
            truth = set(deals[(deals["owner_rep_id"] == rep) & deals["proposal_date"].notna()
                              & (deals["is_closed"] == "True")]["deal_id"])
        elif name == "duplicate_leads":
            truth = set(planted[(planted["leak_id"] == name) & (planted["record_type"] == "lead")]["record_id"])
        else:
            truth = set(planted.loc[planted["leak_id"] == name, "record_id"])
        r = prf(found, truth)
        r["usd_found"] = summary["checks"][name]["usd"]
        r["usd_planted"] = key_leaks[name]["usd"]
        r["false_pos_ids"] = sorted(found - truth)[:20]
        r["missed_ids"] = sorted(truth - found)[:20]
        results[name] = r

    # ---- duplicates in more depth: right original? right exact/fuzzy label? the 12 deals?
    dup_rows = findings[findings["check"] == "duplicate_leads"].copy()
    dup_rows["original"] = dup_rows["detail"].str.extract(r"duplicate of (L\d+)")[0]
    dup_rows["type"] = dup_rows["detail"].str.extract(r"^(exact|fuzzy)")[0]
    merged = pairs.merge(dup_rows, left_on="duplicate_lead_id", right_on="record_id", how="left")
    dup_detail = {"right_original": int((merged["original"] == merged["original_lead_id"]).sum()),
                  "right_match_type": int((merged["type"] == merged["match_type"]).sum()),
                  "pairs": len(pairs)}
    for t in ("exact", "fuzzy"):
        sub = merged[merged["match_type"] == t]
        dup_detail[f"recall_{t}"] = float(sub["record_id"].notna().mean())
    dup_deal_truth = set(planted[(planted["leak_id"] == "duplicate_leads")
                                 & (planted["record_type"] == "deal")]["record_id"])
    dup_deal_found = set(dup_rows["detail"].str.extract(r"open deal (D\d+)")[0].dropna())
    dup_detail["duplicate_open_deals"] = prf(dup_deal_found, dup_deal_truth)
    results["duplicate_leads"]["detail"] = dup_detail

    # ---- the fuzzy-duplicate trade-off
    leads, _ = check.load()
    leads = leads.sort_values("lead_id").reset_index(drop=True)
    truth_dups = set(pairs["duplicate_lead_id"])

    # Ablation on the real sample: what each matching signal finds on its own.
    ck = leads["company"].map(check.norm_company)
    dom = leads["email"].map(lambda e: check.email_parts(e)[1])
    loc = leads["email"].map(lambda e: check.email_parts(e)[0].replace(".", ""))
    last = leads["contact_name"].map(lambda n: check.name_tokens(n)[1])

    def flagged(mask) -> dict:
        r = prf(set(leads[mask]["lead_id"]), truth_dups)
        return {k: r[k] for k in ("found", "true_pos", "false_pos", "precision", "recall")}

    full = set(check.find_duplicates(leads)["duplicate_lead_id"])
    ablation = {
        "exact strings (company + name + email)": flagged(leads.duplicated(["company", "contact_name", "email"])),
        "email domain only, public domains allowed": flagged(dom.ne("") & pd.DataFrame({"d": dom}).duplicated("d")),
        "normalised company + normalised email": flagged(dom.ne("") & pd.DataFrame({"c": ck, "l": loc, "d": dom}).duplicated()),
        "normalised company + exact surname": flagged(pd.DataFrame({"c": ck, "s": last}).duplicated()),
        "normalised company only": flagged(pd.DataFrame({"c": ck}).duplicated()),
        "check.py rule (company block + email or fuzzy name)": {
            k: v for k, v in prf(full, truth_dups).items()
            if k in ("found", "true_pos", "false_pos", "precision", "recall")},
    }

    # Stress test. In the sample every company has one real contact, so "same company" alone is
    # enough. Real CRMs hold several people per account. Add 300 colleague rows (a different
    # person, borrowed from another lead, at an existing company) and count how many get merged.
    # Every flagged colleague is a false positive; the 90 planted duplicates are still in the set.
    rng = pd.Series(range(len(leads))).sample(frac=1, random_state=7).tolist()
    base = leads[leads["lead_id"] <= "L01910"].sample(300, random_state=11).reset_index(drop=True)
    donors = leads.loc[rng[:300]].reset_index(drop=True)
    colleagues = base.copy()
    colleagues["lead_id"] = [f"L9{i:04d}" for i in range(300)]
    colleagues["contact_name"] = donors["contact_name"].values
    new_emails = []
    for b, d in zip(base["email"], donors["email"]):
        _, bdom = check.email_parts(b)
        dloc, _ = check.email_parts(d)
        new_emails.append(f"{dloc}@{bdom}" if bdom and dloc else b)
    colleagues["email"] = new_emails
    # drop the rare donor that is the same person as the base lead
    same = [check.same_person(b, c, 0.8)[0] for (_, b), (_, c) in zip(base.iterrows(), colleagues.iterrows())]
    colleagues = colleagues[[not s for s in same]]
    stressed = pd.concat([leads, colleagues], ignore_index=True)
    colleague_ids = set(colleagues["lead_id"])

    sweep = []
    for t in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        d = check.find_duplicates(stressed, name_threshold=t)
        found = set(d["duplicate_lead_id"])
        tp = len(found & truth_dups)
        fp = len(found & colleague_ids) + len(found - truth_dups - colleague_ids)
        sweep.append({"name_threshold": t, "found": len(found), "true_pos": tp, "false_pos": fp,
                      "missed": len(truth_dups) - tp, "precision": tp / len(found) if found else 1.0,
                      "recall": tp / len(truth_dups)})
    company_only_fp = int(pd.DataFrame({"c": stressed["company"].map(check.norm_company)})
                          .duplicated().sum()) - len(truth_dups)
    stress = {"colleagues_added": len(colleagues), "company_only_false_pos": company_only_fp,
              "sweep": sweep}
    naive = ablation["exact strings (company + name + email)"]

    usd_found = summary["total_usd"]
    usd_planted = key["summary"]["total_leak_usd"]
    record_checks = [n for n in check.CHECK_ORDER]
    tp = sum(results[n]["true_pos"] for n in record_checks)
    fnd = sum(results[n]["found"] for n in record_checks)
    pl = sum(results[n]["planted"] for n in record_checks)
    out = {"usd_found": usd_found, "usd_planted": usd_planted,
           "usd_pct_of_planted": usd_found / usd_planted,
           "overall": {"found": fnd, "planted": pl, "true_pos": tp,
                       "precision": tp / fnd, "recall": tp / pl},
           "by_check": results, "duplicate_ablation": ablation,
           "duplicate_stress_test": stress,
           "flagged_reps": summary["context"]["rep_proposal_to_won"]["flagged"]}
    with open(OUT / "evaluation.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")

    # ---- print
    print(f"Found ${usd_found:,.2f} of ${usd_planted:,.2f} planted leakage "
          f"({usd_found / usd_planted:.1%}). Records: precision {tp / fnd:.1%}, recall {tp / pl:.1%}.\n")
    print("| Check | Found | Planted | False pos | Missed | Precision | Recall | USD found | USD planted |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for n in record_checks:
        r = results[n]
        print(f"| `{n}` | {r['found']} | {r['planted']} | {r['false_pos']} | {r['missed']} | "
              f"{r['precision']:.0%} | {r['recall']:.0%} | ${r['usd_found']:,.0f} | ${r['usd_planted']:,.0f} |")
    print(f"| **Total** | {fnd} | {pl} | {fnd - tp} | {pl - tp} | {tp / fnd:.0%} | {tp / pl:.0%} | "
          f"${usd_found:,.0f} | ${usd_planted:,.0f} |")
    d = dup_detail
    print(f"\nDuplicates: exact recall {d['recall_exact']:.0%}, fuzzy recall {d['recall_fuzzy']:.0%}; "
          f"right original {d['right_original']}/{d['pairs']}; right exact/fuzzy label "
          f"{d['right_match_type']}/{d['pairs']}; duplicate open deals "
          f"{d['duplicate_open_deals']['true_pos']}/{d['duplicate_open_deals']['planted']}.")
    print(f"Flagged weak reps: {out['flagged_reps']} "
          f"(planted: {key_leaks['weak_rep_proposal_conversion']['detail']['rep_id']})")
    print("\nDuplicate matching ablation (real sample, 90 planted duplicates):")
    print("| Rule | Flagged | True pos | False pos | Precision | Recall |")
    print("|---|---:|---:|---:|---:|---:|")
    for name, r in ablation.items():
        print(f"| {name} | {r['found']} | {r['true_pos']} | {r['false_pos']} | "
              f"{r['precision']:.0%} | {r['recall']:.0%} |")
    print(f"\nStress test: +{stress['colleagues_added']} colleague rows (different person, same company). "
          f"'Normalised company only' would merge {stress['company_only_false_pos']} of them.")
    print("| Surname threshold | Flagged | True pos | False pos | Missed | Precision | Recall |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for r in sweep:
        print(f"| {r['name_threshold']:.2f} | {r['found']} | {r['true_pos']} | {r['false_pos']} | "
              f"{r['missed']} | {r['precision']:.0%} | {r['recall']:.0%} |")
    print(f"\nWrote {(OUT / 'evaluation.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
