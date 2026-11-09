"""Overview: the total leak, the pipeline it sits in, and what to fix first."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import loaders
import ui


def leak_table() -> pd.DataFrame:
    summary = loaders.hygiene_summary()
    rows = [
        {
            "check": cid,
            "leak": ui.LEAKS[cid]["name"],
            "usd": c["usd"],
            "records": c["count"],
            "basis": ui.LEAKS[cid]["basis"],
            "fix": ui.LEAKS[cid]["fix"],
        }
        for cid, c in summary["checks"].items()
    ]
    return pd.DataFrame(rows).sort_values("usd", ascending=False).reset_index(drop=True)


def render() -> None:
    summary = loaders.hygiene_summary()
    kpi = loaders.pipeline_kpis()
    leaks = leak_table()
    total = summary["total_usd"]
    measured = leaks.loc[leaks["basis"] == "measured", "usd"].sum()

    st.markdown(
        '<div class="rle-eyebrow">Revenue leak audit · snapshot 31 Oct 2026</div>'
        f'<div class="rle-hero"><span class="num">{ui.usd(total)}</span> leaking from a '
        f'{ui.usd_short(kpi["open_usd"])} pipeline</div>'
        f'<div class="rle-sub">Six automated checks over {kpi["leads"]:,} leads and {kpi["deals"]:,} deals '
        f'flagged {summary["total_records"]:,} records. {ui.usd(measured)} is measured pipeline at risk '
        f'(stalled and double-counted deals); {ui.usd(total - measured)} is modelled lost revenue. '
        f"The top two fixes cover {ui.pct(leaks['usd'].head(2).sum() / total)} of it.</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Won revenue", ui.usd(kpi["won_usd"]), help=f'{kpi["won_deals"]} won deals', border=True)
    c2.metric("Open pipeline", ui.usd(kpi["open_usd"]), help=f'{kpi["open_deals"]} open deals', border=True)
    c3.metric(
        "Win rate",
        ui.pct(kpi["win_rate"], 1),
        help=f'{kpi["won_deals"]} won of {kpi["closed_deals"]} closed deals',
        border=True,
    )
    c4.metric(
        "Median first response",
        f'{kpi["median_first_response_min"]:.0f} min',
        help=f'Inbound leads that got a reply ({kpi["inbound_answered"]:,}). The median hides the tail: '
        f'{summary["checks"]["slow_first_response"]["count"]} waited over an hour.',
        border=True,
    )

    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown("#### Leak by type")
        chart_df = leaks.iloc[::-1]  # largest on top in a horizontal bar
        fig = go.Figure()
        for basis, colour, label in [
            ("measured", ui.ACCENT, "Measured: real deal amounts"),
            ("estimated", ui.ACCENT_SOFT, "Estimated: modelled from win-rate gaps"),
        ]:
            part = chart_df[chart_df["basis"] == basis]
            fig.add_bar(
                y=part["leak"],
                x=part["usd"],
                orientation="h",
                name=label,
                marker_color=colour,
                text=[ui.usd(v) for v in part["usd"]],
                textposition="outside",
                textfont=dict(color=ui.INK),
                cliponaxis=False,
                customdata=part[["records"]],
                hovertemplate="<b>%{y}</b><br>%{x:$,.0f} at risk<br>%{customdata[0]:,} records<extra></extra>",
            )
        fig.update_yaxes(categoryorder="array", categoryarray=list(chart_df["leak"]), gridcolor="rgba(0,0,0,0)")
        fig.update_xaxes(showticklabels=False, range=[0, leaks["usd"].max() * 1.28])
        ui.chart(ui.style(fig, height=430, legend=True))
        st.caption("Source: `hygiene/output/summary.json`. Valuation rules are in the hygiene README.")

    with right:
        st.markdown("#### What to fix first")
        items = "".join(
            f'<div class="rle-fix"><span class="rank">{i + 1}</span>'
            f'<span class="name">{r.leak}<span class="rle-tag">{r.records:,} records</span></span>'
            f'<span class="usd">{ui.usd(r.usd)}</span><span class="what">{r.fix}</span></div>'
            for i, r in enumerate(leaks.itertuples())
        )
        st.markdown(items, unsafe_allow_html=True)

    ev = loaders.hygiene_evaluation()
    if ev:
        o = ev["overall"]
        st.caption(
            f"Checked against the planted answer key: {o['true_pos']:,} of {o['planted']:,} records found, "
            f"{o['found'] - o['true_pos']} false positives, {ui.pct(ev['usd_pct_of_planted'])} of planted dollars. "
            "On synthetic data built to these rules, that is the expected result, not a boast."
        )
