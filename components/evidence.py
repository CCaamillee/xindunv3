from __future__ import annotations

import html

import streamlit as st


COLORS = {"supporting": "#D64545", "counter": "#23865B", "missing": "#D9912B"}


def render_evidence_list(items: list[dict], kind: str) -> None:
    if not items:
        st.markdown('<div class="empty-state">暂无证据</div>', unsafe_allow_html=True)
        return
    color = COLORS.get(kind, "#176BCE")
    for item in items:
        st.markdown(
            f"""
            <div class="evidence-card" style="--evidence-color:{color}">
              <div class="evidence-title">{html.escape(item['title'])}</div>
              <div class="evidence-detail">{html.escape(item['detail'])}</div>
              <div class="evidence-meta">{html.escape(item['time'])} · {html.escape(item['source'])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

