"""Reps: proposal-to-win by rep with the outlier called out, and slow vs fast response win rates."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import loaders
import ui


def scorecard() -> pd.DataFrame:
    l, d, r = loaders.leads(), loaders.deals(), loaders.reps()
    by_rep = loaders.hygiene_summary()["context"]["rep_proposal_to_won"]["by_rep"]
    inbound = l[l["channel"] == "inbound"]
    rows = []
    for rep in r.itertuples():
        rd = d[d["owner_rep_id"] == rep.rep_id]
        closed = rd[rd["is_closed"]]
        stats = by_rep[rep.rep_id]
        rows.append(
            {
                "Rep": rep.rep_id,
                "Name": rep.rep_name,
                "Region": rep.region,
                "Leads": int((l["owner_rep_id"] == rep.rep_id).sum()),
                "Median reply (min)": inbound.loc[inbound["owner_rep_id"] == rep.rep_id, "first_response_minutes"].median(),
                "Deals": len(rd),
                "Won $": float(rd.loc[rd["is_won"], "amount_usd"].sum()),
                "Win rate": rd["is_won"].sum() / len(closed) if len(closed) else None,
                "Proposal→won": stats["rate"],
                "z vs team": stats["z"],
            }
        )
    return pd.DataFrame(rows)


def render() -> None:
    ctx = loaders.hygiene_summary()["context"]
    rep_ctx = ctx["rep_proposal_to_won"]
    by_rep = rep_ctx["by_rep"]
    flagged = rep_ctx["flagged"]
    names = loaders.reps().set_index("rep_id")["rep_name"]
    weak_usd = loaders.hygiene_summary()["checks"]["weak_rep_proposal_conversion"]["usd"]
    slow = ctx["slow_first_response"]

    weak = flagged[0] if flagged else None
    best = max(by_rep, key=lambda k: by_rep[k]["rate"])
    so_what = (
        f"{weak} turns {ui.pct(by_rep[weak]['rate'])} of proposals into wins against {ui.pct(by_rep[weak]['rest_rate'])} "
        f"for the rest of the team, a {ui.usd(weak_usd)} gap; {best} shows what good looks like at "
        f"{ui.pct(by_rep[best]['rate'])}. Separately, slow replies cut the win rate by more than half."
        if weak
        else "No rep is a statistically significant outlier on proposal-to-win."
    )
    ui.header("Reps", "One rep, one habit", so_what)

    left, right = st.columns([1.4, 1], gap="large")
    with left:
        ids = sorted(by_rep)
        total_won = sum(v["won"] for v in by_rep.values())
        total_closed = sum(v["closed"] for v in by_rep.values())
        team = total_won / total_closed
        colours = [ui.HIGHLIGHT if r in flagged else ui.NEUTRAL for r in ids]
        fig = go.Figure(
            go.Bar(
                x=[f"{r}<br><sub>{names[r]}</sub>" for r in ids],
                y=[by_rep[r]["rate"] for r in ids],
                marker_color=colours,
                text=[f"{ui.pct(by_rep[r]['rate'])}<br>{by_rep[r]['won']}/{by_rep[r]['closed']}" for r in ids],
                textposition="outside",
                cliponaxis=False,
                customdata=[[by_rep[r]["z"], by_rep[r]["rest_rate"]] for r in ids],
                hovertemplate="<b>%{x}</b><br>Proposal→won %{y:.0%}<br>Rest of team %{customdata[1]:.0%}"
                "<br>z = %{customdata[0]:+.2f}<extra></extra>",
            )
        )
        fig.add_hline(
            y=team, line=dict(color=ui.INK_3, width=1, dash="dot"),
            annotation_text=f"team {ui.pct(team)}", annotation_position="top left",
            annotation_font=dict(color=ui.INK_2, size=12),
        )
        fig.update_layout(title="Proposal → won, closed deals that reached proposal")
        fig.update_yaxes(tickformat=".0%", range=[0, 0.9])
        ui.chart(ui.style(fig, height=380))
        st.caption(
            f"Flagged at z ≤ {rep_ctx['z_threshold']:.0f} (two-proportion test vs the rest of the team). "
            "R03 is low too but not significant at 34 deals; R05 is a positive outlier, not a leak."
        )

    with right:
        cats = [f"Answered within {slow['sla_minutes']} min", f"Answered after {slow['sla_minutes']} min"]
        rates = [slow["fast_win_rate"], slow["slow_win_rate"]]
        counts = [(slow["fast_won"], slow["fast_closed_deals"]), (slow["slow_won"], slow["slow_closed_deals"])]
        fig = go.Figure(
            go.Bar(
                x=cats,
                y=rates,
                marker_color=[ui.ACCENT, ui.HIGHLIGHT],
                text=[f"{ui.pct(r)}<br>{w}/{n} won" for r, (w, n) in zip(rates, counts)],
                textposition="outside",
                cliponaxis=False,
                hovertemplate="<b>%{x}</b><br>Win rate %{y:.1%}<extra></extra>",
            )
        )
        fig.update_layout(title="Win rate, inbound leads by first-response speed")
        fig.update_yaxes(tickformat=".0%", range=[0, max(rates) * 1.35])
        ui.chart(ui.style(fig, height=380))
        st.caption("Closed deals from inbound leads. Correlation, not proof: the data can't show the delay caused the loss.")

    st.markdown("#### Rep scorecard")
    st.dataframe(
        scorecard(),
        hide_index=True,
        column_config={
            "Won $": st.column_config.NumberColumn(format=ui.USD_FMT),
            "Win rate": st.column_config.NumberColumn(format="percent"),
            "Proposal→won": st.column_config.NumberColumn(format="percent"),
            "Median reply (min)": st.column_config.NumberColumn(format="%.0f", help="Inbound leads that got a reply"),
            "z vs team": st.column_config.NumberColumn(format="%+.2f"),
        },
    )
