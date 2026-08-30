from __future__ import annotations

import hmac
import os

import streamlit as st


def _configured_password() -> str:
    environment_value = os.getenv("APP_ACCESS_PASSWORD", "").strip()
    if environment_value:
        value = environment_value
    else:
        try:
            secret_value = st.secrets.get("APP_ACCESS_PASSWORD", "")
            value = str(secret_value).strip() if secret_value else ""
        except Exception:
            value = ""
    if any(term in value.lower() for term in ("replace", "替换", "changeme", "example")):
        return ""
    return value


def require_access() -> bool:
    """Apply an optional shared-password gate for private demonstrations."""
    expected = _configured_password()
    if not expected:
        return True
    if st.session_state.get("access_authenticated") is True:
        return True

    st.markdown("## 智能心盾 · 受限访问")
    st.caption("该演示包含脱敏临床数据摘要，请输入教师或项目组共享的访问密码。")
    with st.form("access_login", border=True):
        supplied = st.text_input("访问密码", type="password")
        submitted = st.form_submit_button("进入系统", type="primary", width="stretch")
    if submitted:
        if hmac.compare_digest(supplied, expected):
            st.session_state.access_authenticated = True
            st.rerun()
        else:
            st.error("访问密码不正确。")
    return False
