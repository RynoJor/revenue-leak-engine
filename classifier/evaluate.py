"""Score classifier/output/labels.csv against the hidden ground truth in replies.csv.

This is the only place in the classifier that reads `true_label`.

Writes classifier/output/eval.json (machine-readable) and classifier/output/eval.md (for humans).

Usage (from the repo root):
    python classifier/evaluate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import (  # noqa: E402
    LABELS, LABELS_CSV, MODEL, OUT_DIR, PROMPT_VERSION, REPLIES_CSV, ROOT, load_cache, usage_cost,
)

EVAL_JSON = OUT_DIR / "eval.json"
EVAL_MD = OUT_DIR / "eval.md"
N_MISTAKES = 10


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def pick_mistakes(wrong: pd.DataFrame, n: int) -> pd.DataFrame:
    """The most instructive mistakes: one per confusion pair first (most common pair first), most
    confident error within each pair, then fill up with the next most confident errors."""
    if wrong.empty:
        return wrong
    wrong = wrong.sort_values(["confidence", "reply_id"], ascending=[False, True])
    pair_counts = wrong.groupby(["true_label", "predicted_label"]).size().sort_values(ascending=False)
    picked: list[str] = []
    while len(picked) < n and len(picked) < len(wrong):
        added = False
        for (t, p), _ in pair_counts.items():
            pool = wrong[(wrong.true_label == t) & (wrong.predicted_label == p) & ~wrong.reply_id.isin(picked)]
            if not pool.empty and len(picked) < n:
                picked.append(pool.iloc[0].reply_id)
                added = True
        if not added:
            break
    return wrong.set_index("reply_id").loc[picked].reset_index()


def main() -> int:
    if not LABELS_CSV.exists():
        print(f"{LABELS_CSV.relative_to(ROOT)} not found. Run classifier/classify.py first.", file=sys.stderr)
        return 1
    preds = pd.read_csv(LABELS_CSV, dtype={"reply_id": str, "lead_id": str})
    truth = pd.read_csv(REPLIES_CSV, dtype=str, keep_default_na=False)
    df = truth.merge(preds, on=["reply_id", "lead_id"], how="inner")
    n, n_total = len(df), len(truth)
    df["correct"] = df.true_label == df.predicted_label
    acc = df.correct.mean()

    per_class = {}
    for lab in LABELS:
        tp = int(((df.predicted_label == lab) & (df.true_label == lab)).sum())
        fp = int(((df.predicted_label == lab) & (df.true_label != lab)).sum())
        fn = int(((df.predicted_label != lab) & (df.true_label == lab)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[lab] = {"support": tp + fn, "predicted": tp + fp, "precision": round(prec, 4),
                          "recall": round(rec, 4), "f1": round(f1, 4)}
    macro_f1 = sum(c["f1"] for c in per_class.values()) / len(LABELS)

    cm = pd.crosstab(df.true_label, df.predicted_label).reindex(index=LABELS, columns=LABELS, fill_value=0)

    # Calibration: is the model's stated confidence worth anything?
    bins = [0, 0.7, 0.9, 0.95, 1.0001]
    names = ["< 0.70", "0.70-0.89", "0.90-0.94", ">= 0.95"]
    df["conf_bin"] = pd.cut(df.confidence, bins=bins, labels=names, right=False)
    calib = (df.groupby("conf_bin", observed=False)
               .agg(n=("correct", "size"), accuracy=("correct", "mean")).reset_index())

    wrong = df[~df.correct]
    mistakes = pick_mistakes(wrong, N_MISTAKES)

    cache = load_cache()
    tin, tout, cost = usage_cost({k: v for k, v in cache.items() if k in set(df.reply_id)})

    # Revenue view: interested replies the classifier would have filed somewhere else.
    missed_interested = int(((df.true_label == "interested") & (df.predicted_label != "interested")).sum())

    result = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "n_scored": n,
        "n_total": n_total,
        "accuracy": round(acc, 4),
        "macro_f1": round(macro_f1, 4),
        "n_wrong": int(len(wrong)),
        "missed_interested": missed_interested,
        "per_class": per_class,
        "confusion_matrix": {"rows_true_cols_predicted": LABELS, "matrix": cm.values.tolist()},
        "calibration": [{"confidence": r.conf_bin, "n": int(r.n),
                         "accuracy": None if r.n == 0 else round(float(r.accuracy), 4)}
                        for r in calib.itertuples()],
        "usage": {"input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost, 4)},
        "mistakes": [{k: (str(v) if k != "confidence" else float(v)) for k, v in m.items()}
                     for m in mistakes[["reply_id", "subject", "reply_text", "true_label",
                                        "predicted_label", "confidence", "reason"]].to_dict("records")],
    }
    EVAL_JSON.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")

    md = [
        "# Reply classifier eval",
        "",
        f"**{pct(acc)} accuracy on {n:,} replies** ({len(wrong)} wrong), macro F1 {macro_f1:.3f}. "
        f"Model `{MODEL}`, prompt `{PROMPT_VERSION}`. API cost USD {cost:.4f} "
        f"({tin:,} input + {tout:,} output tokens).",
        "",
    ]
    if n < n_total:
        md += [f"> Partial run: {n} of {n_total} replies scored.", ""]
    md += [
        f"Interested replies filed under another label: **{missed_interested}** of "
        f"{per_class['interested']['support']}. These are the expensive mistakes: a buyer who said yes and got no follow-up.",
        "",
        "## Per class",
        "",
        "| Label | Support | Predicted | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for lab, c in per_class.items():
        md.append(f"| `{lab}` | {c['support']} | {c['predicted']} | {pct(c['precision'])} | "
                  f"{pct(c['recall'])} | {c['f1']:.3f} |")
    md += ["", "## Confusion matrix", "", "Rows are the true label, columns the predicted label.", "",
           "| true \\ predicted | " + " | ".join(f"`{l}`" for l in LABELS) + " |",
           "|---|" + "---:|" * len(LABELS)]
    for lab in LABELS:
        md.append(f"| `{lab}` | " + " | ".join(
            (f"**{v}**" if lab == col else (str(v) if v else "·")) for col, v in cm.loc[lab].items()) + " |")
    md += ["", "## Is the confidence score useful?", "", "| Stated confidence | Replies | Accuracy |",
           "|---|---:|---:|"]
    for r in result["calibration"]:
        md.append(f"| {r['confidence']} | {r['n']} | {'' if r['accuracy'] is None else pct(r['accuracy'])} |")
    md += ["", f"## The {len(mistakes)} most instructive mistakes", "",
           "One per type of confusion first (most common first), the most confident error of each type.", ""]
    for i, m in enumerate(result["mistakes"], 1):
        body = m["reply_text"].replace("\n", " ").replace("|", "/")
        md += [f"{i}. **{m['reply_id']}**: true `{m['true_label']}`, predicted `{m['predicted_label']}` "
               f"(confidence {m['confidence']:.2f})",
               f"   - Subject: {m['subject']}",
               f"   - Reply: \"{body}\"",
               f"   - Model's reason: {m['reason']}"]
    md.append("")
    EVAL_MD.write_text("\n".join(md), encoding="utf-8", newline="\n")

    print(f"Accuracy {pct(acc)} on {n} replies ({len(wrong)} wrong), macro F1 {macro_f1:.3f}, cost USD {cost:.4f}")
    for lab, c in per_class.items():
        print(f"  {lab:14s} P {pct(c['precision']):>6s}  R {pct(c['recall']):>6s}  n={c['support']}")
    print(f"Wrote {EVAL_MD.relative_to(ROOT)} and {EVAL_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
