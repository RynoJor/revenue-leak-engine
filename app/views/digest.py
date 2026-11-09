"""Weekly digest: renders the latest AI-written summary, if the digest job has produced one."""

from __future__ import annotations

import re

import streamlit as st

import loaders
import ui


def render() -> None:
    ui.header(
        "Weekly digest",
        "The Monday-morning summary",
        "A short written brief of what leaked this week and who should act on it, so a sales leader gets the "
        "point without opening a dashboard.",
    )
    text = loaders.digest_markdown()
    if text is None:
        st.info("Digest not generated yet. It will appear here once `digest/output/latest.md` is committed.",
                icon=":material/schedule:")
        return
    # Demote the digest's own headings two levels so they sit under this page's title.
    text = re.sub(r"^(#{1,4}) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", text, flags=re.M)
    with st.container(border=True):
        st.markdown(text)
    st.caption("Source: `digest/output/latest.md`, generated offline. The dashboard only displays it.")
