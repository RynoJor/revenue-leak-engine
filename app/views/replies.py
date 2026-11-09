"""Replies: what the classifier sorted, how accurate and cheap it was, and the interested queue."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import loaders
import ui

LABELS = {
    "interested": "Interested",
    "not_now": "Not now",
    "out_of_office": "Out of office",
    "referral": "Referral",
    "unsubscribe": "Unsubscribe",
}
REVIEW_BELOW = 0.90  # confidence band where every error sat (classifier README)


def render() -> None:
    ev = loaders.classifier_eval()
    labels = loaders.reply_labels()
    interested = labels[labels["predicted_label"] == "interested"]

    ui.header(
        "Replies",
        f"{len(interested):,} buying signals pulled out of {len(labels):,} replies",
        f"An LLM sorts every reply into one of five buckets, so reps open the interested ones first instead of "
        f"reading the inbox top to bottom. It missed {ev['missed_interested']} interested replies and cost "
        f"{ui.usd(ev['usage']['cost_usd'], 2)} for the lot.",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accuracy", ui.pct(ev["accuracy"], 1), help=f"{ev['n_wrong']} wrong of {ev['n_scored']:,}", border=True)
    c2.metric("Macro F1", f"{ev['macro_f1']:.3f}", border=True)
    c3.metric("API cost, all replies", ui.usd(ev["usage"]["cost_usd"], 2),
              help=f"{ev['usage']['input_tokens']:,} input + {ev['usage']['output_tokens']:,} output tokens", border=True)
    c4.metric("Cost per dashboard visit", "$0.00", help="The app reads cached labels; it never calls the API.", border=True)

    left, right = st.columns([1.1, 1], gap="large")
    with left:
        mix = labels["predicted_label"].value_counts().reindex(list(LABELS)).fillna(0)
        order = mix.sort_values()
        fig = go.Figure(
            go.Bar(
                y=[LABELS[k] for k in order.index], x=order.values, orientation="h",
                marker_color=[ui.ACCENT if k == "interested" else ui.NEUTRAL for k in order.index],
                text=[f"{int(n):,} · {n / len(labels):.0%}" for n in order.values],
                textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>%{x:,} replies<extra></extra>",
            )
        )
        fig.update_layout(title="Predicted label mix")
        fig.update_xaxes(showticklabels=False, range=[0, order.max() * 1.25])
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        ui.chart(ui.style(fig, height=300))
    with right:
        st.markdown(f"**Model:** `{ev['model']}` · prompt `{ev['prompt_version']}`")
        cal = [c for c in ev["calibration"] if c["n"]]
        st.markdown(
            "**Confidence is honest:** "
            + "; ".join(f"{c['confidence']}: {c['n']:,} replies, {ui.pct(c['accuracy'], 1)} right" for c in cal)
            + f". Anything under {REVIEW_BELOW:.2f} should get a human glance."
        )
        m = ev["mistakes"][0] if ev["mistakes"] else None
        if m:
            st.markdown(
                f"**All {ev['n_wrong']} errors are one sentence**, labelled *not now* by the data and read as "
                f"*interested* by the model:"
            )
            core = max(m["reply_text"].splitlines(), key=len)  # the body line, without greeting or sign-off
            st.markdown(f"> {core}")
            st.caption("Synthetic replies reuse ~300 template sentences, so expect lower accuracy on a real inbox.")

    st.markdown("#### Interested replies queue")
    rep = loaders.replies()[["reply_id", "received_at", "subject", "reply_text"]]
    lead = loaders.leads()[["lead_id", "company", "contact_name", "owner_rep_id"]]
    queue = interested.merge(rep, on="reply_id").merge(lead, on="lead_id").sort_values("received_at", ascending=False)
    queue["check"] = queue["confidence"] < REVIEW_BELOW
    only_review = st.toggle(f"Only show replies under {REVIEW_BELOW:.2f} confidence (worth a second look)")
    if only_review:
        queue = queue[queue["check"]]
    st.caption(f"{len(queue):,} replies, newest first")
    st.dataframe(
        queue[["received_at", "company", "contact_name", "owner_rep_id", "subject", "reply_text", "confidence", "reason", "check"]],
        hide_index=True,
        height=420,
        column_config={
            "received_at": st.column_config.DatetimeColumn("Received", format="YYYY-MM-DD HH:mm"),
            "company": "Company",
            "contact_name": "Contact",
            "owner_rep_id": "Owner",
            "subject": "Subject",
            "reply_text": st.column_config.TextColumn("Reply", width="large"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
            "reason": st.column_config.TextColumn("Model's reason", width="medium"),
            "check": st.column_config.CheckboxColumn("Review", help=f"Confidence under {REVIEW_BELOW:.2f}"),
        },
    )
