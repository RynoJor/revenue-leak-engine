"""Data hygiene: counts and dollars per check, and the filterable findings list."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import loaders
import ui


def render() -> None:
    summary = loaders.hygiene_summary()
    findings = loaders.hygiene_findings()
    ev = loaders.hygiene_evaluation()
    names = {k: v["name"] for k, v in ui.LEAKS.items()}

    ui.header(
        "Data hygiene",
        f"{summary['total_records']:,} records need attention",
        "Every finding names the record, says what is wrong in plain words and carries the dollars at risk, "
        "so a RevOps owner can work the list top-down instead of arguing about data quality in general.",
    )

    per = pd.DataFrame(
        [
            {
                "Check": names[cid],
                "id": cid,
                "Records": c["count"],
                "USD at risk": round(c["usd"]),
                "Basis": ui.LEAKS[cid]["basis"],
                "Precision": ev["by_check"][cid]["precision"] if ev else None,
                "Recall": ev["by_check"][cid]["recall"] if ev else None,
            }
            for cid, c in summary["checks"].items()
        ]
    ).sort_values("USD at risk", ascending=False)

    left, right = st.columns([0.85, 1.25], gap="large")
    with left:
        ordered = per.sort_values("Records")
        fig = go.Figure(
            go.Bar(
                y=ordered["Check"], x=ordered["Records"], orientation="h", marker_color=ui.ACCENT,
                text=ordered["Records"], textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>%{x:,} records<extra></extra>",
            )
        )
        fig.update_layout(title="Records flagged per check")
        fig.update_xaxes(showticklabels=False, range=[0, ordered["Records"].max() * 1.2])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        ui.chart(ui.style(fig, height=300))
    with right:
        st.markdown("&nbsp;")
        st.dataframe(
            per.drop(columns=["id", "Basis"]),
            hide_index=True,
            column_config={
                "USD at risk": st.column_config.NumberColumn(format=ui.USD_FMT),
                "Precision": st.column_config.NumberColumn(format="percent", help="vs the planted answer key"),
                "Recall": st.column_config.NumberColumn(format="percent", help="vs the planted answer key"),
            },
        )
        st.caption(
            "Many records, few dollars (slow replies) vs few records, many dollars (stalled proposals): "
            "rank by dollars, not by count."
        )

    st.markdown("#### Findings")
    f1, f2, f3 = st.columns([1.6, 1.3, 0.9])
    picked = f1.multiselect(
        "Check", options=list(per["id"]), default=[], format_func=names.get, placeholder="All checks"
    )
    query = f2.text_input("Search record or detail", placeholder="e.g. L00031, R02, missing email")
    only_usd = f3.toggle("Only rows with $ at risk", value=False)

    view = findings[findings["check"].isin(picked or per["id"])]
    if query:
        q = query.strip().lower()
        view = view[view["record_id"].str.lower().str.contains(q, regex=False) | view["detail"].str.lower().str.contains(q, regex=False)]
    if only_usd:
        view = view[view["usd_at_risk"] > 0]
    view = view.sort_values("usd_at_risk", ascending=False).assign(check=lambda x: x["check"].map(names))

    st.caption(f"{len(view):,} findings · {ui.usd(view['usd_at_risk'].sum())} at risk")
    st.dataframe(
        view.assign(usd_at_risk=view["usd_at_risk"].round()),  # display rounds; the CSV download keeps cents
        hide_index=True,
        height=420,
        column_config={
            "check": "Check",
            "record_id": "Record",
            "detail": st.column_config.TextColumn("What's wrong", width="large"),
            "usd_at_risk": st.column_config.NumberColumn("USD at risk", format=ui.USD_FMT),
        },
    )
    st.download_button(
        "Download these findings (CSV)", view.to_csv(index=False).encode("utf-8"),
        file_name="hygiene_findings.csv", mime="text/csv", icon=":material/download:",
    )
    st.caption(
        "Estimated dollars are spread evenly over the flagged rows so the list sums to the summary. "
        "Fuzzy duplicate merges should still get a human glance before anything is deleted."
    )
