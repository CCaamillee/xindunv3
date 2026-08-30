from __future__ import annotations

import html

import streamlit as st


def render_record_cards(
    records: list[dict],
    empty_message: str = "暂无该类记录",
    *,
    show_source: bool = True,
) -> None:
    if not records:
        st.markdown(f"<div class='empty-state'>{html.escape(empty_message)}</div>", unsafe_allow_html=True)
        return
    for index, item in enumerate(records):
        label = html.escape(str(item.get("field") or "未命名字段"))
        value = str(item.get("value") or "暂无记录")
        source = html.escape(str(item.get("source") or "工作簿"))
        source_markup = (
            f"<div class='record-source'>{source}</div>" if show_source else ""
        )
        if item.get("is_long"):
            preview = html.escape(value[:220] + ("…" if len(value) > 220 else ""))
            st.markdown(
                f"<div class='record-card'><div class='record-label'>{label}</div>"
                f"<div class='record-value'>{preview}</div>"
                f"{source_markup}</div>",
                unsafe_allow_html=True,
            )
            with st.expander(
                f"展开全文 · {item.get('field')}",
                icon=":material/article:",
                key=f"record_{index}_{item.get('field')}",
            ):
                st.write(value)
                if show_source:
                    st.caption(source)
        else:
            st.markdown(
                f"<div class='record-card'><div class='record-label'>{label}</div>"
                f"<div class='record-value'>{html.escape(value)}</div>"
                f"{source_markup}</div>",
                unsafe_allow_html=True,
            )
