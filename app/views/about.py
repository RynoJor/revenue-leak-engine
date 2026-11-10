"""About: what the project is, how the pieces fit, and where to read the code."""

from __future__ import annotations

import streamlit as st

import ui

ARCHITECTURE = """
digraph G {
  rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.7; size="11,4";
  node [shape=box, style="rounded,filled", fillcolor="white", color="#c4c2bb",
        fontname="Helvetica", fontsize=13, fontcolor="#1b1b1a", margin="0.18,0.08"];
  edge [color="#8a8983", arrowsize=0.6];

  crm [label="Synthetic CRM export\\ndata/sample/*.csv\\nleads · deals · replies · reps", fillcolor="#f3f2ef"];
  hyg [label="hygiene/\\n6 leak checks + $"];
  sco [label="scoring/\\nexplainable lead score"];
  cls [label="classifier/\\nClaude Haiku reply labels"];
  rou [label="router/\\nspeed-to-lead routing"];
  dig [label="digest/\\nweekly AI brief"];
  out [label="committed outputs\\nCSV + JSON + Markdown", fillcolor="#f3f2ef"];
  app [label="app/\\nthis dashboard\\n(no API calls)", color="#2a78d6", penwidth=1.6];
  key [label="answer_key/\\nevals only", style="rounded,dashed", fontcolor="#52514e"];

  crm -> {hyg sco cls rou};
  hyg -> dig; sco -> dig; cls -> dig;
  {hyg sco cls rou dig} -> out -> app;
  key -> hyg [style=dashed, label=" score after", fontsize=9, fontcolor="#8a8983"];
}
"""

MODULES = [
    ("Data", "docs/SCHEMA.md", "Seeded generator, schema and the planted-leak answer key."),
    ("Hygiene check", "hygiene/README.md", "Six checks that find and price each leak; scored against the key."),
    ("Lead scoring", "scoring/README.md", "Ridge logistic model, time-split validation, reasons per lead."),
    ("Reply classifier", "classifier/README.md", "Claude Haiku with a strict JSON schema, cache and eval."),
    ("Speed-to-lead router", "router/README.md", "Validate, dedupe, enrich, score, assign, alert; timed per stage."),
    ("Weekly digest", "digest/README.md", "An AI-written weekly brief built from the module outputs."),
    ("Dashboard", "app/README.md", "This app: Streamlit + Plotly over committed files."),
]


def render() -> None:
    ui.header(
        "About",
        "Revenue Leak Engine",
        "A working GTM-engineering toolkit that finds where a B2B pipeline loses money, puts a dollar figure on "
        "each leak and ships the fixes: scoring, reply triage, instant routing and a weekly brief.",
    )

    st.markdown("#### How it fits together")
    st.graphviz_chart(ARCHITECTURE, width="stretch")
    st.caption(
        "Each module reads the CRM export and writes its results to `output/`. The dashboard only reads "
        "those files, so it runs with no keys and costs nothing per visit. Only the evals open the answer key."
    )

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("#### Synthetic data, built to show the method")
        st.markdown(
            "Every lead, company, rep and dollar is invented by a seeded generator (seed 42, snapshot 31 Oct 2026) "
            "that plants six known leaks. That makes every number here checkable against an answer key, and it "
            "also means the scores are an upper bound: real CRMs are messier. Each module README says where its "
            "number would drop on real data."
        )
    with right:
        st.markdown("#### Read the code")
        st.markdown(
            "\n".join(f"- [{name}]({ui.REPO}/blob/main/{path}): {what}" for name, path, what in MODULES)
            + f"\n\n[Repository on GitHub]({ui.REPO})"
        )
        st.markdown("#### Built by")
        st.markdown(
            f"**Ryno Jordaan**, GTM engineer. [Portfolio]({ui.PORTFOLIO}) · [LinkedIn]({ui.LINKEDIN}) · [GitHub]({ui.REPO})\n\n"
            "Stack: Python, pandas, numpy, Claude API (offline, cached), Streamlit, Plotly."
        )
