from __future__ import annotations

import streamlit as st


def render_header(title: str, description: str = "", eyebrow: str = "智能心盾") -> None:
    st.markdown(
        f"""
        <div class="page-header">
          <div class="page-eyebrow">{eyebrow}</div>
          <div class="page-title">{title}</div>
          {f'<div class="page-description">{description}</div>' if description else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str) -> None:
    st.markdown(
        f'<div class="section-head"><div class="section-title">{title}</div></div>',
        unsafe_allow_html=True,
    )
