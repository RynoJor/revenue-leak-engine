"""Funnel: stage conversion, where deals are lost, time in stage, and the stalled proposals."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import loaders
import ui

LABEL = {"qualified": "Qualified", "demo": "Demo", "proposal": "Proposal", "negotiation": "Negotiation"}


def stage_table() -> pd.DataFrame:
    """Per open stage: reached, moved on, lost there, still sitting there, and conversion of decided deals."""
    d = loaders.deals()
    order = loaders.STAGES
    rows = []
    for i, s in enumerate(order):
        reached = d[f"{s}_date"].notna()
        later = order[i + 1 :]
        moved = d["is_won"] | (d[[f"{x}_date" for x in later]].notna().any(axis=1) if later else False)
        moved = reached & moved
        lost = reached & (d["lost_at_stage"] == s)
        still = reached & (d["stage"] == s)
        decided = moved.sum() + lost.sum()
        rows.append(
            {
                "Stage": LABEL[s],
                "Reached": int(reached.sum()),
                "Moved on": int(moved.sum()),
                "Lost here": int(lost.sum()),
                "Still open here": int(still.sum()),
                "Conversion": moved.sum() / decided if decided else None,
                "Open $ here": float(d.loc[still, "amount_usd"].sum()),
            }
        )
    return pd.DataFrame(rows)


def render() -> None:
    d = loaders.deals()
    l = loaders.leads()
    findings = loaders.hygiene_findings()
    stalled_usd = loaders.hygiene_summary()["checks"]["stalled_proposals"]["usd"]
    table = stage_table()
    won = int(d["is_won"].sum())
    stalled_ids = findings.loc[findings["check"] == "stalled_proposals", "record_id"]

    ui.header(
        "Funnel",
        "Deals die early, and proposals go quiet",
        f"Of {len(d):,} deals opened, {won} were won. Most losses happen at qualified and demo, and "
        f"{len(stalled_ids)} proposals worth {ui.usd(stalled_usd)} have had no activity for 30+ days.",
    )

    left, right = st.columns([1, 1], gap="large")
    with left:
        stages = ["Leads", "Qualified", "Demo", "Proposal", "Negotiation", "Won"]
        values = [len(l), *table["Reached"].tolist(), won]
        fig = go.Figure(
            go.Funnel(
                y=stages,
                x=values,
                marker=dict(color=ui.ACCENT),
                connector=dict(fillcolor="#e7eef8", line=dict(width=0)),
                text=[f"{values[0]:,}"] + [f"{v:,} · {v / p:.0%}" for p, v in zip(values, values[1:])],
                texttemplate="%{text}",
                textposition="auto",
                insidetextfont=dict(color="white"),
                outsidetextfont=dict(color=ui.INK),
                hovertemplate="<b>%{y}</b><br>%{x:,} reached this stage<br>%{percentPrevious:.0%} of the previous stage<extra></extra>",
            )
        )
        fig.update_layout(title="Reached each stage")
        ui.chart(ui.style(fig, height=360))
        st.caption("Includes deals still in progress, so later stages will fill in further.")
    with right:
        lost = d[d["lost_at_stage"].notna()]
        by_stage = lost.groupby("lost_at_stage").size().reindex(loaders.STAGES).fillna(0)
        fig = go.Figure(
            go.Bar(
                x=[LABEL[s] for s in by_stage.index],
                y=by_stage.values,
                marker_color=ui.NEUTRAL,
                text=by_stage.astype(int).values,
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>%{y} deals lost here<extra></extra>",
            )
        )
        fig.update_layout(title="Where lost deals were lost")
        fig.update_yaxes(showticklabels=False, showgrid=False)
        ui.chart(ui.style(fig, height=360))

    st.markdown("#### Stage by stage")
    st.dataframe(
        table,
        hide_index=True,
        column_config={
            "Conversion": st.column_config.ProgressColumn(
                "Conversion", help="Moved on ÷ (moved on + lost). Open deals are left out.", format="percent",
                min_value=0, max_value=1,
            ),
            "Open $ here": st.column_config.NumberColumn("Open $ here", format=ui.USD_FMT),
        },
    )

    left, right = st.columns([1, 1], gap="large")
    with left:
        tis = loaders.time_in_stage()
        fig = go.Figure()
        for s in loaders.STAGES:
            days = tis.loc[tis["stage"] == s, "days"]
            fig.add_box(
                x=days, name=LABEL[s], marker_color=ui.ACCENT, line=dict(width=1.5), boxpoints=False,
                hovertemplate=f"<b>{LABEL[s]}</b><br>%{{x}} days<extra></extra>",
            )
        fig.update_layout(title="Days in stage (deals that left the stage)")
        fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
        fig.update_xaxes(showgrid=True, gridcolor=ui.GRID, title_text="days")
        ui.chart(ui.style(fig, height=320))
        med = tis.groupby("stage")["days"].median().reindex(loaders.STAGES)
        st.caption(
            "Median days: " + " · ".join(f"{LABEL[s]} {med[s]:.0f}" for s in loaders.STAGES)
            + ". A stalled proposal has sat 30+ days with no activity at all."
        )
    with right:
        reasons = lost["lost_reason"].value_counts().sort_values()
        fig = go.Figure(
            go.Bar(
                y=reasons.index, x=reasons.values, orientation="h", marker_color=ui.NEUTRAL,
                text=reasons.values, textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>%{x} deals<extra></extra>",
            )
        )
        fig.update_layout(title="Why deals were lost")
        fig.update_xaxes(showticklabels=False)
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        ui.chart(ui.style(fig, height=320))

    st.markdown(f"#### Stalled proposals ({len(stalled_ids)}, {ui.usd(stalled_usd)})")
    reps = loaders.reps().set_index("rep_id")["rep_name"]
    stalled = d[d["deal_id"].isin(stalled_ids)].copy()
    stalled["days_idle"] = (pd.Timestamp(loaders.AS_OF) - stalled["last_activity_date"]).dt.days
    stalled["rep"] = stalled["owner_rep_id"] + " · " + stalled["owner_rep_id"].map(reps)
    stalled = stalled.sort_values("amount_usd", ascending=False)
    st.dataframe(
        stalled[["deal_id", "company", "rep", "region", "amount_usd", "proposal_date", "last_activity_date", "days_idle"]],
        hide_index=True,
        column_config={
            "deal_id": "Deal",
            "company": "Company",
            "rep": "Owner",
            "region": "Region",
            "amount_usd": st.column_config.NumberColumn("Amount", format=ui.USD_FMT),
            "proposal_date": st.column_config.DateColumn("Proposal sent"),
            "last_activity_date": st.column_config.DateColumn("Last activity"),
            "days_idle": st.column_config.NumberColumn("Days idle"),
        },
    )
