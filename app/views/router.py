"""Router: routing time vs the historical wait, per-stage timing and the replay log."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import loaders
import ui

STAGES = ["validate", "dedupe", "enrich", "score", "assign", "alert"]


def render() -> None:
    s = loaders.routing_summary()
    log = loaders.routing_log()
    ms = s["routing_ms_median"]
    wait_min = s["historical_first_response_minutes_median"]

    ui.header(
        "Router",
        f"Routed in {ms:.0f} ms vs a {wait_min:.0f}-minute median first response",
        f"Replaying the last {s['leads_replayed']} inbound leads: the router validates, dedupes, scores and assigns an "
        f"owner in milliseconds, which removes the 'whose lead is it?' delay. {s['historical_over_60_min']} of these "
        f"leads waited over an hour for a reply and {s['historical_never_responded']} never got one.",
    )

    st.markdown(
        '<div class="rle-vs">'
        f'<div class="card accent"><div class="big">{ms:.1f} ms</div>'
        f'<div class="lbl">median time from form to owner alert (max {s["routing_ms_max"]:.1f} ms)</div></div>'
        f'<div class="card warn"><div class="big">{wait_min:.0f} min</div>'
        f'<div class="lbl">historical median first response for the same leads</div></div>'
        f'<div class="card"><div class="big">{s["historical_over_60_min"] + s["historical_never_responded"]} of {s["leads_replayed"]}</div>'
        f'<div class="lbl">waited over an hour or never got a reply</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Honest framing: the router gets the lead to an owner, it doesn't reply for them. A person still has to "
        "answer; this removes the routing delay so any delay left is the rep's. Timings are wall-clock and move a little per run."
    )

    routed = log[log["status"] == "routed"]
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        med = routed[[f"{x}_ms" for x in STAGES]].median()
        fig = go.Figure(
            go.Bar(
                y=STAGES[::-1], x=[med[f"{x}_ms"] for x in STAGES[::-1]], orientation="h",
                marker_color=[ui.ACCENT if x == "dedupe" else ui.ACCENT_SOFT for x in STAGES[::-1]],
                text=[f"{med[f'{x}_ms']:.2f} ms" for x in STAGES[::-1]],
                textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>median %{x:.3f} ms<extra></extra>",
            )
        )
        fig.update_layout(title="Median time per pipeline stage")
        fig.update_xaxes(showticklabels=False, range=[0, med.max() * 1.3])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        ui.chart(ui.style(fig, height=300))
        st.caption("Dedupe dominates: a fuzzy company-name search over ~1,900 accounts. An exact email hit skips it.")
    with right:
        st.markdown(f"**What happened to the {s['leads_replayed']} leads**")
        st.markdown(
            f"- **{s['routed']}** routed, **{s['rejected']}** rejected (no usable email)\n"
            f"- **{s['dedupe_hits']}** were duplicates and went straight to their existing owner\n"
            f"- **{s['over_capacity']}** fell back to the least-loaded rep\n"
            "- Assigned per rep: "
            + ", ".join(f"{r} {n}" for r, n in sorted(s["assigned_by_rep"].items()))
        )
        st.markdown(
            "**Stubbed on purpose:** enrichment is a keyword table and the fit score is a short rule set. "
            "Production would add a form webhook, a queue with retries and a CRM write-back."
        )

    st.markdown("#### Replay log")
    st.dataframe(
        log.fillna({"dedupe_match": "", "flags": "", "assigned_rep": "", "assign_reason": ""})[["lead_id", "status", "region", "assigned_rep", "assign_reason", "score", "total_ms", "dedupe_match",
             "flags", "historical_owner_rep_id", "historical_first_response_minutes"]],
        hide_index=True,
        height=380,
        column_config={
            "lead_id": "Lead",
            "status": "Status",
            "region": "Region",
            "assigned_rep": "Routed to",
            "assign_reason": "Why",
            "score": "Fit score",
            "total_ms": st.column_config.NumberColumn("Routing ms", format="%.2f"),
            "dedupe_match": "Duplicate of",
            "flags": "Flags",
            "historical_owner_rep_id": "Owner in CRM",
            "historical_first_response_minutes": st.column_config.NumberColumn(
                "Actual first reply (min)", format="%d", help="Empty = never answered"
            ),
        },
    )
