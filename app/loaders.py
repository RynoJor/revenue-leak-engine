"""Read-only loaders for the committed module outputs.

The dashboard never calls an API and never recomputes a module's result: it reads the CSV and
JSON files each module wrote, so the numbers here are the numbers in the module READMEs.
Every loader is wrapped in ``st.cache_data`` so a page switch costs no disk reads.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
AS_OF = date(2026, 10, 31)  # snapshot date used by every module (docs/SCHEMA.md)

STAGES = ["qualified", "demo", "proposal", "negotiation"]


def _json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


# ---- raw CRM export ---------------------------------------------------------------------------


@st.cache_data
def leads() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE / "leads.csv", dtype={"first_response_minutes": "Int64"})
    df["created_at"] = pd.to_datetime(df["created_at"])
    return df


@st.cache_data
def deals() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE / "deals.csv")
    for col in [c for c in df.columns if c.endswith("_date")]:
        df[col] = pd.to_datetime(df[col])
    return df


@st.cache_data
def stage_history() -> pd.DataFrame:
    df = pd.read_csv(SAMPLE / "deal_stage_history.csv")
    df["entered_date"] = pd.to_datetime(df["entered_date"])
    return df


@st.cache_data
def replies() -> pd.DataFrame:
    # true_label is the eval's ground truth; the dashboard shows predictions only.
    df = pd.read_csv(SAMPLE / "replies.csv").drop(columns=["true_label"])
    df["received_at"] = pd.to_datetime(df["received_at"])
    return df


@st.cache_data
def reps() -> pd.DataFrame:
    return pd.read_csv(SAMPLE / "reps.csv")


# ---- module outputs ---------------------------------------------------------------------------


@st.cache_data
def hygiene_summary() -> dict:
    return _json("hygiene/output/summary.json")


@st.cache_data
def hygiene_evaluation() -> dict | None:
    path = ROOT / "hygiene/output/evaluation.json"
    return _json("hygiene/output/evaluation.json") if path.exists() else None


@st.cache_data
def hygiene_findings() -> pd.DataFrame:
    return pd.read_csv(ROOT / "hygiene/output/findings.csv")


@st.cache_data
def scores() -> pd.DataFrame:
    return pd.read_csv(ROOT / "scoring/output/scores.csv").fillna({"reason_1": "", "reason_2": "", "reason_3": ""})


@st.cache_data
def scoring_validation() -> dict:
    return _json("scoring/output/validation.json")


@st.cache_data
def reply_labels() -> pd.DataFrame:
    return pd.read_csv(ROOT / "classifier/output/labels.csv")


@st.cache_data
def classifier_eval() -> dict:
    return _json("classifier/output/eval.json")


@st.cache_data
def routing_log() -> pd.DataFrame:
    return pd.read_csv(ROOT / "router/output/routing_log.csv")


@st.cache_data
def routing_summary() -> dict:
    return _json("router/output/routing_summary.json")


@st.cache_data(ttl=300)
def digest_markdown() -> str | None:
    """The weekly digest is produced by a separate job; it may not exist yet."""
    path = ROOT / "digest/output/latest.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


# ---- derived views (pure pandas over the files above) ------------------------------------------


@st.cache_data
def pipeline_kpis() -> dict:
    d, l = deals(), leads()
    won = d[d["is_won"]]
    closed = d[d["is_closed"]]
    open_ = d[~d["is_closed"]]
    inbound = l[(l["channel"] == "inbound") & l["first_response_minutes"].notna()]
    return {
        "won_usd": float(won["amount_usd"].sum()),
        "won_deals": len(won),
        "open_usd": float(open_["amount_usd"].sum()),
        "open_deals": len(open_),
        "closed_deals": len(closed),
        "win_rate": len(won) / len(closed),
        "median_first_response_min": float(inbound["first_response_minutes"].median()),
        "inbound_answered": len(inbound),
        "leads": len(l),
        "deals": len(d),
    }


@st.cache_data
def time_in_stage() -> pd.DataFrame:
    """Days spent in each open stage, for deals that have left it (moved on, won or lost)."""
    order = {s: i for i, s in enumerate(STAGES + ["won", "lost"])}
    h = stage_history().copy()
    h["order"] = h["stage"].map(order)
    h = h.sort_values(["deal_id", "entered_date", "order"])
    h["next_entered"] = h.groupby("deal_id")["entered_date"].shift(-1)
    h = h[h["stage"].isin(STAGES) & h["next_entered"].notna()]
    h["days"] = (h["next_entered"] - h["entered_date"]).dt.days
    return h[["deal_id", "stage", "days"]]
