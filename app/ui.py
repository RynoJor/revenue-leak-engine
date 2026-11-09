"""Shared look: colours, number formats, page header and the plotly layout every chart uses."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

# One accent, one highlight for "look here", one neutral. Text never wears a series colour.
ACCENT = "#2a78d6"
ACCENT_SOFT = "#a9c9f0"
HIGHLIGHT = "#eb6834"
NEUTRAL = "#c4c2bb"
INK = "#1b1b1a"
INK_2 = "#52514e"
INK_3 = "#8a8983"
GRID = "#ebeae6"
SURFACE = "#fcfcfb"
USD_FMT = "$%,d"  # dataframe NumberColumn format: thousands separators, no cents
FONT = '"Source Sans", "Source Sans Pro", "Segoe UI", system-ui, sans-serif'

REPO = "https://github.com/RynoJor/revenue-leak-engine"
LINKEDIN = "https://www.linkedin.com/in/ryno-jordaan/"

# The six hygiene checks, in the vocabulary a sales leader uses.
LEAKS = {
    "stalled_proposals": {
        "name": "Stalled proposals",
        "basis": "measured",
        "fix": "Review every proposal idle for 30+ days this week: re-engage or close it out so the forecast is real.",
    },
    "weak_rep_proposal_conversion": {
        "name": "Weak proposal-to-win rep",
        "basis": "estimated",
        "fix": "Pair R02 with R05 (71% proposal-to-win) for proposal reviews and call coaching.",
    },
    "slow_first_response": {
        "name": "Slow first response",
        "basis": "estimated",
        "fix": "Route inbound leads to an owner instantly and hold a 60-minute reply SLA.",
    },
    "duplicate_leads": {
        "name": "Duplicate leads",
        "basis": "measured",
        "fix": "Merge the duplicates and drop the double-counted open deals from the forecast.",
    },
    "never_followed_up": {
        "name": "Never followed up",
        "basis": "estimated",
        "fix": "Work the untouched inbound leads now; auto-escalate any lead unanswered after 24 hours.",
    },
    "missing_required_fields": {
        "name": "Missing required fields",
        "basis": "estimated",
        "fix": "Make the 8 required fields mandatory on the form; backfill the leads with no email first.",
    },
}

CSS = f"""
<style>
  .block-container {{ padding-top: 2.2rem; max-width: 1180px; }}
  h1, h2, h3 {{ letter-spacing: -0.01em; }}
  .rle-banner {{
    display: inline-block; font-size: 0.78rem; color: {INK_2};
    background: #f3f2ef; border: 1px solid {GRID}; border-radius: 999px;
    padding: 0.18rem 0.75rem; margin-bottom: 0.6rem;
  }}
  .rle-eyebrow {{ font-size: 0.8rem; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: {INK_3}; margin: 0 0 0.25rem 0; }}
  .rle-title {{ font-size: 2rem; font-weight: 700; line-height: 1.15; color: {INK}; margin: 0; }}
  .rle-sowhat {{ font-size: 1.05rem; color: {INK_2}; margin: 0.5rem 0 1.4rem 0;
    border-left: 3px solid {ACCENT}; padding-left: 0.8rem; max-width: 60rem; }}
  .rle-hero {{ font-size: 2.6rem; font-weight: 750; line-height: 1.1; color: {INK};
    letter-spacing: -0.02em; margin: 0.2rem 0 0.4rem 0; }}
  .rle-hero .num {{ color: {HIGHLIGHT}; }}
  .rle-sub {{ font-size: 1.02rem; color: {INK_2}; margin-bottom: 1.4rem; max-width: 62rem; }}
  .rle-fix {{ display: grid; grid-template-columns: 2rem 1fr auto; gap: 0.2rem 0.8rem;
    align-items: baseline; padding: 0.7rem 0; border-bottom: 1px solid {GRID}; }}
  .rle-fix .rank {{ font-weight: 700; color: {INK_3}; font-variant-numeric: tabular-nums; }}
  .rle-fix .name {{ font-weight: 650; color: {INK}; }}
  .rle-fix .usd {{ font-weight: 700; color: {INK}; font-variant-numeric: tabular-nums; text-align: right; }}
  .rle-fix .what {{ grid-column: 2 / 4; color: {INK_2}; font-size: 0.93rem; }}
  .rle-tag {{ font-size: 0.72rem; font-weight: 600; color: {INK_2}; border: 1px solid {GRID};
    border-radius: 4px; padding: 0 0.35rem; margin-left: 0.4rem; vertical-align: 1px; }}
  .rle-vs {{ display: flex; gap: 1.2rem; flex-wrap: wrap; margin: 0.4rem 0 1.2rem 0; }}
  .rle-vs .card {{ flex: 1 1 15rem; border: 1px solid {GRID}; border-radius: 10px;
    padding: 1rem 1.2rem; background: white; }}
  .rle-vs .big {{ font-size: 2.4rem; font-weight: 750; letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums; color: {INK}; }}
  .rle-vs .lbl {{ color: {INK_2}; font-size: 0.92rem; }}
  .rle-vs .accent {{ border-top: 4px solid {ACCENT}; }}
  .rle-vs .warn {{ border-top: 4px solid {HIGHLIGHT}; }}
  div[data-testid="stMetric"] {{ background: white; }}
</style>
"""


def usd(x: float, decimals: int = 0) -> str:
    return f"${x:,.{decimals}f}"


def usd_short(x: float) -> str:
    if abs(x) >= 1e6:
        return f"${x / 1e6:,.1f}M"
    if abs(x) >= 1e3:
        return f"${x / 1e3:,.0f}K"
    return f"${x:,.0f}"


def pct(x: float, decimals: int = 0) -> str:
    return f"{x * 100:.{decimals}f}%"


def header(eyebrow: str, title: str, so_what: str) -> None:
    st.markdown(
        f'<div class="rle-eyebrow">{eyebrow}</div><div class="rle-title">{title}</div>'
        f'<div class="rle-sowhat">{so_what}</div>',
        unsafe_allow_html=True,
    )


def style(fig: go.Figure, height: int = 340, legend: bool = False) -> go.Figure:
    """Recessive axes, light grid, no chart chrome. Applied to every figure."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK_2, size=13, family=FONT),
        title_font=dict(color=INK, size=15, family=FONT),
        title_x=0,
        title_xanchor="left",
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(color=INK, size=13)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="", zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, ticks="", zeroline=False, automargin=True, ticksuffix="  ")
    fig.update_traces(selector=dict(type="bar"), marker_cornerradius=4, marker_line_width=0)
    return fig


def chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, theme=None, config={"displayModeBar": False})
