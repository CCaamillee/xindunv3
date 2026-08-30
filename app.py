from __future__ import annotations

import streamlit as st

from components.access import require_access
from components.footer import render_footer
from components.sidebar import render_navigation
from components.styles import inject_global_styles
from config import APP_NAME
from views import about, clinical_agent, dashboard, home, patient_workspace


st.set_page_config(
    page_title=f"{APP_NAME} · 心脏破裂风险辅助分析系统",
    page_icon="assets/intelligent-heart-shield-logo.png",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_global_styles()

if not require_access():
    st.stop()

st.session_state.setdefault("active_page", "首页")
st.session_state.setdefault("selected_encounter_key", None)
st.session_state.setdefault("chat_history", {})

page = render_navigation()

if st.session_state.get("_last_rendered_page") != page:
    st.html(
        """
        <script>
          const main = document.querySelector('[data-testid="stMain"]');
          if (main) main.scrollTo({top: 0, left: 0, behavior: 'instant'});
        </script>
        """,
        unsafe_allow_javascript=True,
    )
    st.session_state._last_rendered_page = page

ROUTES = {
    "首页": home.render,
    "急诊概览": dashboard.render,
    "辅助诊断": clinical_agent.render,
    "病情详情": patient_workspace.render,
    "关于": about.render,
}

ROUTES.get(page, home.render)()
render_footer()
