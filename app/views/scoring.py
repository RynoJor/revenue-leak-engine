"""Lead scoring: tier win rates on held-out leads, the AUC caveat, and the scored lead list."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import loaders
import ui

TIERS = ["A", "B", "C", "D"]
SPEED = {
    "under_15m": "Answered within 15 min",
    "15_60m": "Answered within the hour",
    "1h_1d": "First reply after 1 hour",
    "over_1d": "First reply after 1 day",
    "never": "Never followed up",
}


def point_label(key: str) -> str:
    kind, _, value = key.partition("=")
    return SPEED.get(value, value) if kind == "speed" else f"Region: {value}"


def render() -> None:
    v = loaders.scoring_validation()
    tiers = v["test_tiers"]
    base = v["base_win_rate_test"]
    lo, hi = v["test_auc_95ci"]

    ui.header(
        "Lead scoring",
        f"A-tier leads win at {v['a_vs_d_lift']:.1f}× the D-tier rate",
        f"On leads the model never saw, A-tier won {ui.pct(tiers['A']['win_rate'], 1)} vs {ui.pct(tiers['D']['win_rate'], 1)} "
        "for D-tier, so reps can work the top of the list first. Every score comes with its reasons.",
    )

    st.warning(
        f"**Read this before trusting a tier.** Test AUC is **{v['test_auc']:.2f}** (95% CI {lo:.2f}–{hi:.2f}): better "
        f"than chance, but a weak signal built on {v['test']['won']} held-out wins. Only two inputs survived selection "
        f"({' and '.join(v['features_kept'])}); industry, size, source and replies added nothing, so they get zero points. "
        f"Forcing all inputs in scored {v['test_auc_all_features_for_comparison']:.2f}. Treat tiers as a rough ordering, not a forecast.",
        icon=":material/info:",
    )

    left, right = st.columns([1.2, 1], gap="large")
    with left:
        fig = go.Figure(
            go.Bar(
                x=[f"Tier {t}" for t in TIERS],
                y=[tiers[t]["win_rate"] for t in TIERS],
                marker_color=[ui.ACCENT if t == "A" else ui.ACCENT_SOFT for t in TIERS],
                text=[f"{ui.pct(tiers[t]['win_rate'], 1)}<br>{tiers[t]['won']}/{tiers[t]['leads']}" for t in TIERS],
                textposition="outside",
                cliponaxis=False,
                hovertemplate="<b>%{x}</b><br>Win rate %{y:.1%}<extra></extra>",
            )
        )
        fig.add_hline(
            y=base, line=dict(color=ui.INK_3, width=1, dash="dot"),
            annotation_text=f"all test leads {ui.pct(base, 1)}", annotation_position="top right",
            annotation_font=dict(color=ui.INK_2, size=12),
        )
        fig.update_layout(title=f"Win rate by tier, held-out leads ({v['test']['created_from']} to {v['test']['created_before']})")
        fig.update_yaxes(tickformat=".0%", range=[0, max(t["win_rate"] for t in tiers.values()) * 1.35])
        ui.chart(ui.style(fig, height=340))
    with right:
        tier_tbl = pd.DataFrame(
            [{"Tier": t, "Leads": tiers[t]["leads"], "Won": tiers[t]["won"], "Win rate": tiers[t]["win_rate"]} for t in TIERS]
        )
        st.markdown("**Held-out tier table**")
        st.dataframe(tier_tbl, hide_index=True, column_config={"Win rate": st.column_config.NumberColumn(format="percent")})
        points = pd.Series(v["points"]).rename_axis("Input").reset_index(name="Points")
        points["Input"] = points["Input"].map(point_label)
        st.markdown("**Points vs an average lead (50)**")
        st.dataframe(points.sort_values("Points", ascending=False), hide_index=True, height=250,
                     column_config={"Points": st.column_config.NumberColumn(format="%+.1f")})

    st.markdown("#### Scored leads")
    s = loaders.scores().merge(
        loaders.leads()[["lead_id", "company", "region", "source", "owner_rep_id", "status", "created_at"]], on="lead_id"
    )
    s["reasons"] = s[["reason_1", "reason_2", "reason_3"]].apply(lambda r: " · ".join(x for x in r if x), axis=1)
    f1, f2, f3 = st.columns([1, 1.2, 1.4])
    tier_pick = f1.multiselect("Tier", TIERS, default=["A"])
    region_pick = f2.multiselect("Region", sorted(s["region"].dropna().unique()), placeholder="All regions")
    q = f3.text_input("Search company or lead", placeholder="e.g. Larkwell")
    view = s[s["tier"].isin(tier_pick or TIERS)]
    if region_pick:
        view = view[view["region"].isin(region_pick)]
    if q:
        ql = q.strip().lower()
        view = view[view["company"].fillna("").str.lower().str.contains(ql, regex=False) | view["lead_id"].str.lower().str.contains(ql, regex=False)]
    view = view.sort_values(["score", "created_at"], ascending=[False, False])
    st.caption(f"{len(view):,} of {len(s):,} leads")
    st.dataframe(
        view[["lead_id", "company", "score", "tier", "reasons", "region", "source", "owner_rep_id", "status"]],
        hide_index=True,
        height=400,
        column_config={
            "lead_id": "Lead",
            "company": "Company",
            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "tier": "Tier",
            "reasons": st.column_config.TextColumn("Why this score", width="large"),
            "region": "Region",
            "source": "Source",
            "owner_rep_id": "Owner",
            "status": "Status",
        },
    )
