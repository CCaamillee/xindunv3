from __future__ import annotations

import html

import streamlit as st

from components.cards import status_badge
from components.evidence import render_evidence_list
from components.header import render_header, section_header
from components.timeline import render_timeline
from services.data_api import get_case_replay, get_patient_page, get_patient_summary


def _move_replay(key: str, delta: int, maximum: int) -> None:
    st.session_state[key] = max(0, min(maximum, st.session_state.get(key, 0) + delta))


def _reset_replay(key: str) -> None:
    st.session_state[key] = 0


def render() -> None:
    render_header("病程回顾")
    lookup_col, lookup_action = st.columns([4, 1], vertical_alignment="bottom")
    lookup_id = lookup_col.text_input(
        "按展示编号载入破裂组病例",
        placeholder="例如 XD-AB12CD34EF",
        key="case_replay_lookup_id",
    )
    if lookup_action.button("载入病例", width="stretch"):
        try:
            lookup_patient = get_patient_summary(lookup_id.strip().upper())
            if lookup_patient["cohort_label"] != 1:
                st.error("病程回顾当前仅支持回顾性心脏破裂组记录。")
            else:
                st.session_state.selected_patient_id = lookup_patient["patient_id"]
        except (KeyError, ValueError):
            st.error("未找到该脱敏展示编号，请检查后重试。")

    requested_page = int(st.session_state.get("case_replay_page", 1))
    patient_page = get_patient_page(page=requested_page, page_size=100, cohort_label=1)
    current_page = int(patient_page["page"])
    total_pages = int(patient_page["total_pages"])
    if requested_page != current_page:
        st.session_state.case_replay_page = current_page
    page_col, page_note = st.columns([1, 4], vertical_alignment="bottom")
    page_col.number_input(
        "病例页码",
        min_value=1,
        max_value=total_pages,
        step=1,
        key="case_replay_page",
    )
    page_note.caption(
        f"回顾性破裂组共 {patient_page['total']:,} 条就诊样本 · 当前第 {current_page}/{total_pages} 页"
    )

    candidates = list(patient_page["items"])
    requested_patient = st.session_state.get("selected_patient_id")
    candidate_ids = {row["patient_id"] for row in candidates}
    if requested_patient and requested_patient not in candidate_ids:
        try:
            requested_summary = get_patient_summary(requested_patient)
            if requested_summary["cohort_label"] == 1:
                candidates.insert(0, requested_summary)
        except KeyError:
            pass
    patient_ids = [row["patient_id"] for row in candidates]
    default_id = requested_patient if requested_patient in patient_ids else patient_ids[0]
    candidate_map = {row["patient_id"]: row for row in candidates}
    selected = st.selectbox(
        "选择回顾性破裂组病例",
        patient_ids,
        index=patient_ids.index(default_id),
        format_func=lambda patient_id: (
            f"{patient_id} · {candidate_map[patient_id]['age']}岁 · {candidate_map[patient_id]['diagnosis']}"
        ),
    )
    st.session_state.selected_patient_id = selected
    replay = get_case_replay(selected)
    snapshots = replay["snapshots"]
    if not snapshots:
        st.warning("该患者没有可用于病程回顾的结构化快照。", icon=":material/event_busy:")
        return

    key = f"replay_step_{selected}"
    st.session_state.setdefault(key, 0)
    max_step = len(snapshots) - 1
    stored_step = int(st.session_state.get(key, 0))
    if stored_step < 0 or stored_step > max_step:
        st.session_state[key] = 0

    if max_step == 0:
        step = 0
        st.info(
            "该患者没有可重建的纵向结构化事件，当前仅展示单个结构化患者快照。",
            icon=":material/info:",
        )
    else:
        step = st.slider("病程时间", 0, max_step, format="时间点 %d", key=key)
    snapshot = snapshots[step]

    if max_step == 0:
        st.caption(f"当前相对时间 {snapshot['time']} · 单一快照 · 无可回放的前后时点")
    else:
        prev_col, next_col, reset_col, meta_col = st.columns([0.8, 0.8, 0.8, 3])
        prev_col.button(
            "← 上一时点",
            width="stretch",
            disabled=step == 0,
            on_click=_move_replay,
            args=(key, -1, max_step),
        )
        next_col.button(
            "下一时点 →",
            type="primary",
            width="stretch",
            disabled=step == max_step,
            on_click=_move_replay,
            args=(key, 1, max_step),
        )
        reset_col.button("重置", width="stretch", on_click=_reset_replay, args=(key,))
        meta_col.caption(
            f"当前相对时间 {snapshot['time']} · 已载入 {len(snapshot['visible_events'])} 个事件"
        )

    section_header("当前病程状态")
    state_col, action_col = st.columns([1.7, 1], vertical_alignment="center")
    with state_col:
        st.markdown(
            f"<div class='info-box'><div class='risk-eyebrow' style='color:#71869A'>当前快照</div>"
            f"<div style='font-size:28px;font-weight:800;margin:.45rem 0'>{snapshot['visible_event_count']} 个可见事件</div>"
            f"<div>{status_badge('不计算时点风险')}</div>"
            f"<div style='font-size:12px;color:#4D657A;margin-top:.8rem'>{html.escape(snapshot['event'])}</div></div>",
            unsafe_allow_html=True,
        )
    with action_col:
        st.info("回顾性结局标签仅用于病例选择，不参与历史时点的风险或信号计算。")
        if st.button("进入辅助研判", width="stretch"):
            st.session_state.selected_patient_id = selected
            st.session_state.agent_scope_selector = selected
            st.session_state.pending_page = "辅助研判"
            st.rerun()

    timeline_col, evidence_col = st.columns([1.5, 1])
    with timeline_col:
        section_header("截至当前时点的临床事件")
        render_timeline(snapshot["visible_events"], expandable=False)
    with evidence_col:
        section_header("当前可见证据")
        evidence_items = [
            {"title": event["title"], "detail": event["summary"], "time": event["time"], "source": event["source"]}
            for event in snapshot["visible_events"]
        ][-5:]
        render_evidence_list(evidence_items, "supporting")
        if step == max_step:
            section_header("数据缺口")
            render_evidence_list(replay["detail"]["evidence"]["missing"], "missing")
    st.markdown(
        "<div class='safe-note'>病程回顾仅展示已记录的相对时间和结构化摘要，并隐藏直接标识。</div>",
        unsafe_allow_html=True,
    )

