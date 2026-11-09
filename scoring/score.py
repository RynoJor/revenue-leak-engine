"""Lead scoring: an explainable 0-100 score for every lead in data/sample/leads.csv.

Method
------
A logistic regression (written in numpy, no extra packages) predicts whether a lead ends in a
won deal. Candidate inputs are fit (industry, company size, region, source) and behaviour
(response speed, replies, buying-signal and opt-out replies, reply recency). Forward selection on
the training period keeps only the inputs that improve cross-validated AUC; the rest get zero
points. The coefficients are turned into points, so every score is a sum you can read:

    score = 50 + sum of points          (clipped to 0-100)

Each feature's points are centred on the average training lead, so 50 means "an average lead" and
"+12" means "12 points better than average on this feature". The three largest moves become the
plain-English reasons.

No peeking
----------
- Behaviour features only use what was known at the scoring point: the earlier of 14 days after
  the lead was created or the day it became a deal. Replies that arrive later are ignored.
- Never used as features: data/sample/answer_key/, replies.true_label, leads.status,
  leads.last_activity_date (it includes the deal's close date), the owner rep, and anything
  from deals.csv except the date the deal opened (to close the window).
- Validation is out of time: fit on leads created before CUTOFF, test on the leads after it.

Run from the repo root:  python scoring/score.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

AS_OF = date(2026, 10, 31)
DATA = Path("data/sample")
OUT = Path("scoring/output")

WINDOW_DAYS = 14                  # behaviour is observed for this long after the lead arrives
CUTOFF = pd.Timestamp("2026-06-01")   # train: created before; test: created on/after
MATURE_BY = pd.Timestamp("2026-09-01")  # leads created later are too young to judge (still scored)
L2_GRID = [1, 3, 10, 30, 100]     # ridge penalties tried; picked by cross-validation on train only
MIN_GAIN = 0.01                   # a feature must lift cross-validated AUC by this much to be kept
POINTS_PER_LOGIT = 20             # +1 log-odds (about 2.7x the odds) = +20 points
TIER_SHARES = {"A": 0.20, "B": 0.30, "C": 0.30}  # rest is D; cut-offs come from training scores

POSITIVE = r"pricing|demo|trial|a call|chat|setup|show me|interested|you have my attention|sign up"
OPT_OUT = r"stop emailing|unsubscribe|remove me|take me off|not interested|do not contact|don't contact"

CATEGORICAL = ["industry", "company_size", "region", "source", "speed", "replies"]
BINARY = ["positive_reply", "opt_out_reply", "recent_reply"]

LABELS = {
    "industry": "Industry: {}",
    "company_size": "Company size {} staff",
    "region": "Region: {}",
    "speed": {
        "under_15m": "Answered within 15 minutes",
        "15_60m": "Answered within the hour",
        "1h_1d": "First response took over an hour",
        "over_1d": "First response took over a day",
        "never": "Never followed up",
    },
    "replies": {"0": "No replies yet", "1": "Replied once", "2+": "Replied two or more times"},
    "positive_reply": ("Asked for pricing, a demo or a call", "No buying-signal reply"),
    "opt_out_reply": ("Asked to stop emails", "Has not opted out"),
    "recent_reply": ("Replied in the last 7 days", "No reply in the last 7 days"),
}
SOURCE_LABELS = {
    "website_form": "Inbound website form", "demo_request": "Inbound demo request",
    "free_trial": "Inbound free trial", "content_download": "Inbound content download",
    "outbound_email": "Outbound email", "referral": "Referral", "event": "Event lead",
    "partner": "Partner lead",
}


# --------------------------------------------------------------------------- features

def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    leads = pd.read_csv(DATA / "leads.csv", dtype=str, keep_default_na=False)
    leads["created_at"] = pd.to_datetime(leads["created_at"])
    leads["first_response_minutes"] = pd.to_numeric(leads["first_response_minutes"])
    deals = pd.read_csv(DATA / "deals.csv", usecols=["lead_id", "qualified_date", "is_closed", "is_won"])
    deals["qualified_date"] = pd.to_datetime(deals["qualified_date"])
    replies = pd.read_csv(DATA / "replies.csv", usecols=["lead_id", "received_at", "subject", "reply_text"])
    replies["received_at"] = pd.to_datetime(replies["received_at"])  # true_label never loaded
    return leads, deals, replies


def speed_band(minutes: float) -> str:
    if pd.isna(minutes):
        return "never"
    if minutes <= 15:
        return "under_15m"
    if minutes <= 60:
        return "15_60m"
    return "1h_1d" if minutes <= 24 * 60 else "over_1d"


def build_features(leads: pd.DataFrame, deals: pd.DataFrame, replies: pd.DataFrame) -> pd.DataFrame:
    df = leads[["lead_id", "industry", "company_size", "region", "source", "created_at"]].copy()
    for col in ["industry", "company_size", "region", "source"]:
        df[col] = df[col].replace("", "Unknown")

    # Scoring point: day 14, or the day the lead became a deal if sooner, never past AS_OF.
    opened = df["lead_id"].map(deals.set_index("lead_id")["qualified_date"])
    point = df["created_at"].dt.normalize() + timedelta(days=WINDOW_DAYS)
    point = point.where(opened.isna() | (opened > point), opened)
    df["score_point"] = point.clip(upper=pd.Timestamp(AS_OF))

    # A first response logged after the scoring point was not known yet.
    first_resp = leads["created_at"] + pd.to_timedelta(leads["first_response_minutes"], unit="m")
    minutes = leads["first_response_minutes"].where(first_resp <= df["score_point"])
    df["speed"] = minutes.map(speed_band)

    r = replies.merge(df[["lead_id", "score_point"]], on="lead_id")
    r = r[r["received_at"] < r["score_point"]].copy()
    auto = r["subject"].str.startswith("Automatic reply")
    text = r["reply_text"].str.lower()
    r["opt_out"] = ~auto & text.str.contains(OPT_OUT, regex=True)
    r["positive"] = ~auto & ~r["opt_out"] & text.str.contains(POSITIVE, regex=True)
    r["recent"] = ~auto & (r["received_at"] >= r["score_point"] - timedelta(days=7))
    r["human"] = ~auto
    per = r.groupby("lead_id").agg(n=("human", "sum"), positive=("positive", "any"),
                                   opt_out=("opt_out", "any"), recent=("recent", "any"))

    n = df["lead_id"].map(per["n"]).fillna(0)
    df["replies"] = np.select([n == 0, n == 1], ["0", "1"], "2+")
    for col, src in [("positive_reply", "positive"), ("opt_out_reply", "opt_out"), ("recent_reply", "recent")]:
        df[col] = df["lead_id"].map(per[src]).fillna(False).astype(int)

    # Outcome (for training and validation only).
    d = deals.set_index("lead_id")
    df["won"] = df["lead_id"].map(d["is_won"]).fillna(False).astype(int)
    df["open_deal"] = df["lead_id"].map(~d["is_closed"]).fillna(False).astype(bool)
    return df


def design(df: pd.DataFrame, levels: dict[str, list[str]] | None = None):
    """One column per category level (full dummies; the ridge penalty keeps it identifiable)."""
    if levels is None:
        levels = {c: sorted(df[c].unique()) for c in CATEGORICAL}
    cols, names = [], []
    for c in CATEGORICAL:
        for lv in levels[c]:
            cols.append((df[c] == lv).to_numpy(float))
            names.append((c, lv))
    for c in BINARY:
        cols.append(df[c].to_numpy(float))
        names.append((c, 1))
    return np.column_stack(cols), names, levels


# --------------------------------------------------------------------------- model

def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float, iters: int = 50) -> tuple[float, np.ndarray]:
    """Ridge logistic regression by Newton's method. The intercept is not penalised."""
    A = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(A.shape[1])
    w[0] = np.log(y.mean() / (1 - y.mean()))
    pen = np.full(A.shape[1], l2)
    pen[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-A @ w))
        grad = A.T @ (p - y) + pen * w
        H = (A * (p * (1 - p))[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(H, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w[0], w[1:]


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """Probability a random winner outscores a random non-winner (ties count half)."""
    ranks = pd.Series(s).rank().to_numpy()
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def auc_interval(y: np.ndarray, s: np.ndarray, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        if 0 < y[i].sum() < len(i):
            vals.append(auc(y[i], s[i]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def cv_auc(X: np.ndarray, y: np.ndarray, l2: float, repeats: int = 5, k: int = 5) -> float:
    """Mean AUC over repeated 5-fold cross-validation. Training data only."""
    scores = []
    for rep in range(repeats):
        folds = np.arange(len(y)) % k
        np.random.default_rng(rep).shuffle(folds)
        for f in range(k):
            m = folds == f
            _, c = fit_logistic(X[~m], y[~m], l2)
            scores.append(auc(y[m], X[m] @ c))
    return float(np.mean(scores))


def columns(names: list, features: list[str]) -> list[int]:
    return [i for i, (c, _) in enumerate(names) if c in features]


def best_l2(X: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    results = {l2: cv_auc(X, y, l2) for l2 in L2_GRID}
    l2 = max(results, key=results.get)
    return l2, results[l2]


def select_features(X: np.ndarray, y: np.ndarray, names: list) -> tuple[list[str], float, list[dict]]:
    """Forward selection: keep adding the feature that most improves cross-validated AUC on the
    training set; stop when nothing adds at least MIN_GAIN. A feature that cannot clear that bar
    gets zero points rather than a weight fitted to noise."""
    chosen, current, log = [], 0.5, []
    candidates = CATEGORICAL + BINARY
    while len(chosen) < len(candidates):
        trials = {f: best_l2(X[:, columns(names, chosen + [f])], y) for f in candidates if f not in chosen}
        best = max(trials, key=lambda f: trials[f][1])
        log.append({"kept_so_far": list(chosen),
                    "cv_auc_if_added": {f: round(v[1], 3) for f, v in trials.items()}})
        if trials[best][1] - current < MIN_GAIN:
            break
        chosen.append(best)
        current = trials[best][1]
    return chosen, current, log


# --------------------------------------------------------------------------- points and reasons

def to_points(X: np.ndarray, coef: np.ndarray, X_train: np.ndarray) -> np.ndarray:
    """Per-lead, per-feature points, centred so the average training lead sits at 0."""
    contrib = X * coef
    return (contrib - (X_train * coef).mean(axis=0)) * POINTS_PER_LOGIT


def group_points(points: np.ndarray, names: list) -> pd.DataFrame:
    """Sum the dummy columns back into one number per original feature."""
    return pd.DataFrame({f: points[:, columns(names, [f])].sum(axis=1) for f in CATEGORICAL + BINARY})


def describe(feature: str, row: pd.Series) -> str:
    value = row[feature]
    if feature == "source":
        return SOURCE_LABELS.get(value, f"Source: {value}")
    if feature in BINARY:
        yes, no = LABELS[feature]
        return yes if value else no
    label = LABELS[feature]
    return label[value] if isinstance(label, dict) else label.format(value)


def reasons(df: pd.DataFrame, pts: pd.DataFrame, k: int = 3) -> list[list[str]]:
    """The k features that moved the score most, largest first. Blank if fewer than k moved it."""
    out = []
    for i in range(len(df)):
        row_pts = pts.iloc[i].round()
        top = row_pts[row_pts != 0].abs().sort_values(ascending=False, kind="stable").index[:k]
        text = [f"{describe(f, df.iloc[i])} ({row_pts[f]:+.0f})" for f in top]
        out.append(text + [""] * (k - len(text)))
    return out


def assign_tier(score: pd.Series, cuts: dict[str, float]) -> pd.Series:
    return pd.Series(np.select([score >= cuts["A"], score >= cuts["B"], score >= cuts["C"]],
                               ["A", "B", "C"], "D"), index=score.index)


def tier_table(scored: pd.DataFrame) -> pd.DataFrame:
    t = scored.groupby("tier").agg(leads=("won", "size"), won=("won", "sum")).reindex(list("ABCD"))
    t["win_rate"] = t["won"] / t["leads"]
    return t


# --------------------------------------------------------------------------- main

def main() -> None:
    leads, deals, replies = load()
    df = build_features(leads, deals, replies)

    # Labelled set: a lead's outcome is known if it has no open deal and is old enough to judge.
    known = ~df["open_deal"] & (df["created_at"] < MATURE_BY)
    train = df[known & (df["created_at"] < CUTOFF)]
    test = df[known & (df["created_at"] >= CUTOFF)]

    X_tr, names, levels = design(train)
    X_te, _, _ = design(test, levels)
    y_tr, y_te = train["won"].to_numpy(), test["won"].to_numpy()

    # Model building: training data only.
    chosen, cv_chosen, selection_log = select_features(X_tr, y_tr, names)
    keep = columns(names, chosen)
    l2, _ = best_l2(X_tr[:, keep], y_tr)
    coef = np.zeros(X_tr.shape[1])
    coef[keep] = fit_logistic(X_tr[:, keep], y_tr, l2)[1]

    def raw_score(X: np.ndarray) -> np.ndarray:
        return 50 + to_points(X, coef, X_tr).sum(axis=1)

    s_tr = raw_score(X_tr)
    cuts, share = {}, 0.0
    for tier, frac in TIER_SHARES.items():
        share += frac
        cuts[tier] = float(np.quantile(s_tr, 1 - share))

    # Held-out test: scored once with the frozen model and frozen tier cut-offs.
    s_te = pd.Series(raw_score(X_te), index=test.index)
    table = tier_table(test.assign(tier=assign_tier(s_te, cuts)))
    a_rate, d_rate = table.loc["A", "win_rate"], table.loc["D", "win_rate"]
    test_auc = auc(y_te, s_te.to_numpy())
    lo, hi = auc_interval(y_te, s_te.to_numpy())

    # For comparison only, never used to choose anything: the same model with every feature in it.
    l2_all, _ = best_l2(X_tr, y_tr)
    full_auc = auc(y_te, X_te @ fit_logistic(X_tr, y_tr, l2_all)[1])

    centre = (X_tr * coef).mean(axis=0)
    report = {
        "train": {"leads": int(len(train)), "won": int(y_tr.sum()), "created_before": str(CUTOFF.date())},
        "test": {"leads": int(len(test)), "won": int(y_te.sum()),
                 "created_from": str(CUTOFF.date()), "created_before": str(MATURE_BY.date())},
        "features_kept": chosen,
        "train_cv_auc": round(cv_chosen, 3),
        "l2": l2,
        "selection_log": selection_log,
        "test_auc": round(test_auc, 3),
        "test_auc_95ci": [round(lo, 3), round(hi, 3)],
        "test_auc_all_features_for_comparison": round(full_auc, 3),
        "tier_cutoffs_from_train": {k: round(v, 2) for k, v in cuts.items()},
        "test_tiers": {t: {"leads": int(r.leads), "won": int(r.won), "win_rate": round(float(r.win_rate), 3)}
                       for t, r in table.iterrows()},
        "a_vs_d_lift": round(float(a_rate / d_rate), 2) if d_rate > 0 else None,
        "base_win_rate_test": round(float(y_te.mean()), 3),
        # Points for having each value, relative to the average training lead's points for that feature.
        "points": {f"{c}={lv}": round(float((coef[i] - centre[columns(names, [c])].sum()) * POINTS_PER_LOGIT), 1)
                   for i, (c, lv) in enumerate(names) if c in chosen},
    }

    # Score every lead with the frozen model and the training tier cut-offs.
    X_all, _, _ = design(df, levels)
    s_all = raw_score(X_all)
    pts = group_points(to_points(X_all, coef, X_tr), names)
    out = pd.DataFrame({"lead_id": df["lead_id"],
                        "score": np.clip(s_all, 0, 100).round().astype(int),
                        "tier": assign_tier(pd.Series(s_all), cuts)})
    out[["reason_1", "reason_2", "reason_3"]] = reasons(df, pts)

    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "scores.csv", index=False, lineterminator="\n")
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"Train {len(train)} leads ({y_tr.sum()} won), test {len(test)} leads ({y_te.sum()} won)")
    print(f"Features kept by forward selection: {chosen} (train CV AUC {cv_chosen:.3f}, ridge {l2})")
    print(f"Test AUC {test_auc:.3f} (95% CI {lo:.2f}-{hi:.2f}); all-features model {full_auc:.3f}")
    print(table.assign(win_rate=table.win_rate.map("{:.1%}".format)).to_string())
    print(f"A-tier wins at {report['a_vs_d_lift']}x the D-tier rate (test base rate {y_te.mean():.1%})")
    print(f"Wrote {OUT / 'scores.csv'} ({len(out)} leads) and {OUT / 'validation.json'}")


if __name__ == "__main__":
    main()
