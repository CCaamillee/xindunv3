from __future__ import annotations

import base64
from datetime import datetime
import html
from functools import lru_cache
from pathlib import Path

import streamlit as st

from config import APP_SUBTITLE, ORGANIZATION_NAME, TEAM_NAME


FOOTER_LOGO_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "navigation-brand-logo.png"
)


@lru_cache(maxsize=1)
def _footer_logo_data_uri() -> str:
    try:
        encoded = base64.b64encode(FOOTER_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{encoded}"


def render_footer() -> None:
    year = datetime.now().year
    logo_uri = _footer_logo_data_uri()
    logo_markup = (
        f'<img class="global-footer-logo" src="{html.escape(logo_uri, quote=True)}" '
        'alt="智能心盾 Logo">'
        if logo_uri
        else '<span class="material-symbols-rounded global-footer-icon" '
        'aria-hidden="true">health_and_safety</span>'
    )
    with st.container(key="global_footer"):
        st.html(
            f"""
            <footer class="global-footer-content" aria-label="系统版权信息">
              <div class="global-footer-brand">
                {logo_markup}
                <div class="global-footer-brand-copy">
                  <strong>{html.escape(APP_SUBTITLE)}</strong>
                  <span>临床辅助与科研分析平台</span>
                </div>
              </div>
              <div class="global-footer-meta">
                <span>© {year} {html.escape(ORGANIZATION_NAME)}</span>
                <span class="global-footer-divider" aria-hidden="true"></span>
                <span>{html.escape(TEAM_NAME)}团队</span>
                <span class="global-footer-divider" aria-hidden="true"></span>
                <span>保留所有权利</span>
              </div>
            </footer>
            """
        )
