from __future__ import annotations

import streamlit as st

from config import PAGE_OPTIONS


LOGO_PATH = "assets/navigation-brand-logo.png"
NAV_ICONS = {
    "首页": ":material/home:",
    "急诊概览": ":material/emergency:",
    "辅助诊断": ":material/clinical_notes:",
    "病情详情": ":material/timeline:",
    "关于": ":material/info:",
}


def _sync_page(source_key: str, target_key: str) -> None:
    selected = st.session_state.get(source_key)
    if selected not in PAGE_OPTIONS:
        return
    st.session_state.active_page = selected
    st.session_state[target_key] = selected


def _select_page(page: str) -> None:
    st.session_state.active_page = page
    st.session_state.mobile_nav_page = page


def render_navigation() -> str:
    pending = st.session_state.pop("pending_page", None)
    if pending in PAGE_OPTIONS:
        st.session_state.active_page = pending

    current = st.session_state.get("active_page", "首页")
    if current not in PAGE_OPTIONS:
        current = "首页"
        st.session_state.active_page = current

    if st.session_state.get("mobile_nav_page") not in PAGE_OPTIONS or pending in PAGE_OPTIONS:
        st.session_state.mobile_nav_page = current

    with st.container(key="top_navigation"):
        brand_col, navigation_col, mobile_col = st.columns(
            [1.25, 5.85, 0.02],
            gap="xsmall",
            vertical_alignment="center",
        )
        with brand_col:
            with st.container(
                key="brand_lockup",
                vertical_alignment="center",
            ):
                st.image(LOGO_PATH, width=180)
        with navigation_col:
            with st.container(
                key="desktop_navigation",
                horizontal=True,
                horizontal_alignment="distribute",
                vertical_alignment="center",
                gap="xsmall",
            ):
                for index, option in enumerate(PAGE_OPTIONS):
                    st.button(
                        option,
                        icon=NAV_ICONS[option],
                        type="primary" if option == current else "tertiary",
                        width="stretch",
                        key=f"desktop_nav_{index}",
                        on_click=_select_page,
                        args=(option,),
                    )
        with mobile_col:
            with st.container(key="mobile_navigation"):
                with st.popover(
                    "菜单",
                    icon=":material/menu:",
                    width="stretch",
                    key="mobile_nav_menu",
                ):
                    st.radio(
                        "主导航",
                        PAGE_OPTIONS,
                        key="mobile_nav_page",
                        on_change=_sync_page,
                        args=("mobile_nav_page", "mobile_nav_page"),
                    )

    return st.session_state.get("active_page", current)
