"""Revenue Leak Engine dashboard.

Run from the repo root:  streamlit run app/main.py

Reads only committed files (data/sample/ and each module's output/). No API calls, no secrets,
so it runs on Streamlit Community Cloud as-is.
"""

from __future__ import annotations

import streamlit as st

import ui
from views import about, digest, funnel, hygiene, overview, replies, reps, router, scoring

st.set_page_config(
    page_title="Revenue Leak Engine",
    page_icon=":material/water_drop:",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(ui.CSS, unsafe_allow_html=True)

pages = {
    "Where the money leaks": [
        st.Page(overview.render, title="Overview", icon=":material/insights:", url_path="overview", default=True),
        st.Page(funnel.render, title="Funnel", icon=":material/filter_alt:", url_path="funnel"),
        st.Page(reps.render, title="Reps", icon=":material/groups:", url_path="reps"),
        st.Page(hygiene.render, title="Data hygiene", icon=":material/cleaning_services:", url_path="hygiene"),
    ],
    "What fixes it": [
        st.Page(scoring.render, title="Lead scoring", icon=":material/leaderboard:", url_path="scoring"),
        st.Page(replies.render, title="Replies", icon=":material/mark_email_unread:", url_path="replies"),
        st.Page(router.render, title="Router", icon=":material/bolt:", url_path="router"),
        st.Page(digest.render, title="Weekly digest", icon=":material/summarize:", url_path="digest"),
    ],
    "Project": [
        st.Page(about.render, title="About", icon=":material/info:", url_path="about"),
    ],
}
page = st.navigation(pages)

with st.sidebar:
    st.caption(
        f"Built by **Ryno Jordaan** · [LinkedIn]({ui.LINKEDIN}) · [GitHub]({ui.REPO})\n\n"
        "Synthetic CRM data, snapshot 31 Oct 2026. Every figure is read from a module's committed output."
    )

st.markdown(
    '<span class="rle-banner">Synthetic data · a fictional B2B SaaS CRM, built to show the method</span>',
    unsafe_allow_html=True,
)
page.run()
