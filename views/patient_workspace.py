from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from components.cards import risk_badge
from components.encounter_filters import render_encounter_filters
from components.header import render_header, section_header
from components.patient_navigation import (
    consume_pending_encounter_key,
    navigate_to_patient_page,
    normalize_encounter_key,
)
from components.records import render_record_cards
from components.timeline import render_timeline
from services.prediction_api import get_prediction_for_encounter, normalize_live_prediction
from services.workbook_data import get_encounter_dataframe, get_encounter_detail


def _encounter_label(encounter_key: str, encounter_map: dict[str, dict]) -> str:
    item = encounter_map[encounter_key]
    diagnosis = str(item.get("diagnosis") or "暂无诊断记录")
    if len(diagnosis) > 48:
        diagnosis = diagnosis[:48] + "…"
    return (
        f"患者 {item['regno']}｜就诊 {item['admno']}｜"
        f"{item['age'] if item['age'] is not None else '暂无年龄'} / {item['gender']}｜{diagnosis}"
    )


def _open_agent(encounter_key: str) -> None:
    navigate_to_patient_page("辅助诊断", encounter_key)


def _render_timeline(detail: dict) -> None:
    events = list(detail["timeline"])
    profile = detail["profile"]
    live_result = st.session_state.get("latest_model_predictions", {}).get(
        profile["encounter_key"]
    ) or get_prediction_for_encounter(profile["encounter_key"])
    normalized = normalize_live_prediction(live_result or {})
    if normalized["available"]:
        events.append(
            {
                "id": f"{profile['encounter_key']}-MODEL",
                "datetime": profile.get("cutoff_datetime"),
                "time": profile.get("cutoff_time") or "预测截点",
                "type": "模型预测",
                "title": "心脏破裂风险模型评估",
                "summary": (
                    f"{normalized['risk_label']}｜未来时间窗：{normalized['prediction_time']}｜"
                    f"证据支持度：{normalized['evidence_confidence']}"
                ),
                "detail": normalized["explanation"] or normalized["answer"],
                "source": "预测模型",
                "source_field": "预测截点前临床资料",
            }
        )
        events.sort(
            key=lambda event: (
                event.get("datetime") is None,
                event.get("datetime") or datetime.max,
            )
        )
    if not events:
        st.markdown(
            "<div class='empty-state'>该次就诊没有可解析的真实时间字段记录。</div>",
            unsafe_allow_html=True,
        )
        return
    st.caption(f"共 {len(events)} 个时间节点，按工作簿中的真实日期时间升序排列。")
    render_timeline(events, expandable=True)


def _render_treatment_and_course(detail: dict) -> None:
    option = st.segmented_control(
        "查看类别",
        ["用药与医嘱", "手术信息", "病程记录"],
        default="用药与医嘱",
        key=f"detail_treatment_group_{detail['profile']['encounter_key']}",
    )
    records = detail["groups"].get(option, [])
    render_record_cards(
        records,
        f"该次就诊暂无{option}字段记录",
        show_source=False,
    )


def _render_risk(detail: dict) -> None:
    profile = detail["profile"]
    live_result = st.session_state.get("latest_model_predictions", {}).get(
        profile["encounter_key"]
    ) or get_prediction_for_encounter(profile["encounter_key"])
    normalized = normalize_live_prediction(live_result or {})
    if normalized["available"]:
        st.markdown(
            f"<div class='risk-legend'><strong>模型风险状态</strong>"
            f"{risk_badge(normalized['risk_level'])}"
            f"<span>预测时间窗：{html.escape(normalized['prediction_time'])}｜"
            f"证据支持度：{html.escape(normalized['evidence_confidence'])}</span></div>",
            unsafe_allow_html=True,
        )
        result_cards = [
            {"field": "模型分类", "value": normalized["classification_label"], "is_long": False},
            {"field": "风险等级", "value": normalized["risk_label"], "is_long": False},
            {"field": "预测时间窗", "value": normalized["prediction_time"], "is_long": False},
            {"field": "证据支持度", "value": normalized["evidence_confidence"], "is_long": False},
            {
                "field": "模型解释",
                "value": normalized["explanation"] or normalized["answer"] or "暂无记录",
                "is_long": True,
            },
        ]
        render_record_cards(result_cards, show_source=False)
        st.info(normalized["notice"], icon=":material/clinical_notes:")
        return
    st.markdown(
        f"<div class='risk-legend'><strong>当前风险状态</strong>{risk_badge(profile['risk_level'])}"
        "<span>风险结果不能只依靠颜色表达；当前文字状态为“无法判断”。</span></div>",
        unsafe_allow_html=True,
    )
    st.warning(
        "当前工作簿没有可验证的模型风险等级或预测破裂时间字段。"
        "系统不会根据 label=1 或 cutoff_time 自行推导高 / 中 / 低风险。",
        icon=":material/warning:",
    )
    render_record_cards(detail["risk"], show_source=False)


def _render_patient_summary(profile: dict) -> None:
    age_gender = (
        f"{profile['age']} 岁 / {profile['gender']}"
        if profile["age"] is not None
        else f"暂无年龄 / {profile['gender']}"
    )
    items = (
        ("年龄 / 性别", age_gender),
        ("入院 / 就诊时间", profile["admission_time"]),
        ("主要诊断", profile["diagnosis"]),
        ("诊断科室", profile["department"]),
    )
    item_markup = "".join(
        "<div class='detail-summary-item'>"
        f"<div class='detail-summary-label'>{html.escape(label)}</div>"
        f"<div class='detail-summary-value'>{html.escape(str(value))}</div>"
        "</div>"
        for label, value in items
    )
    st.html(
        f"""
        <section class="detail-patient-summary" aria-label="患者摘要">
          <div class="detail-summary-heading">
            <div class="detail-summary-icon" aria-hidden="true">
              <span class="material-symbols-rounded">personal_injury</span>
            </div>
            <div>
              <div class="detail-summary-eyebrow">患者资料</div>
              <div class="detail-summary-title">患者 {html.escape(profile['regno'])}</div>
            </div>
            <div class="detail-encounter-chip">
              <span class="material-symbols-rounded" aria-hidden="true">id_card</span>
              就诊编号 {html.escape(profile['admno'])}
            </div>
          </div>
          <div class="detail-summary-grid">{item_markup}</div>
        </section>
        """
    )


def render() -> None:
    with st.container(key="patient_detail_page", gap="small"):
        render_header(
            "病情详情",
            "选择患者的一次就诊，按真实时间字段查看门急诊、入院、检查检验、治疗及出院记录。",
        )
        try:
            with st.skeleton(height=120):
                frame = get_encounter_dataframe()
        except (FileNotFoundError, ImportError, ValueError) as error:
            st.error(f"患者工作簿加载失败：{error}", icon=":material/error:")
            return
        if frame.empty:
            st.info("当前工作簿没有可展示的有效就诊记录。", icon=":material/inbox:")
            return

        pending = consume_pending_encounter_key()
        all_keys = set(frame["encounter_key"])
        if pending in all_keys:
            st.session_state.selected_encounter_key = pending
            st.session_state.detail_encounter_selector = pending

        with st.container(border=True, key="detail_selection_panel"):
            st.subheader(":material/person_search: 患者与就诊选择")
            st.caption("可直接选择就诊记录，也可展开筛选器按患者信息组合查找。")
            filtered = render_encounter_filters(
                frame,
                prefix="detail",
                include_discharge=True,
                expanded=False,
            )
            if filtered.empty:
                st.warning(
                    "没有符合当前组合条件的就诊记录，请调整或清除筛选条件。",
                    icon=":material/search_off:",
                )
                return

            encounter_map = {
                row["encounter_key"]: row.to_dict()
                for _, row in filtered.iterrows()
            }
            options = list(encounter_map)
            requested = normalize_encounter_key(
                st.session_state.get("selected_encounter_key")
            )
            if requested not in encounter_map:
                requested = options[0]
            if st.session_state.get("detail_encounter_selector") not in encounter_map:
                st.session_state.detail_encounter_selector = requested

            selector_col, action_col = st.columns(
                [4.2, 1],
                gap="medium",
                vertical_alignment="bottom",
            )
            selected = selector_col.selectbox(
                "选择患者与就诊记录",
                options,
                format_func=lambda value: _encounter_label(value, encounter_map),
                key="detail_encounter_selector",
                persist_state="session",
            )
            st.session_state.selected_encounter_key = selected
            action_col.button(
                "前往辅助诊断",
                icon=":material/clinical_notes:",
                type="primary",
                width="stretch",
                on_click=_open_agent,
                args=(selected,),
                key=f"detail_to_agent_{selected}",
            )

        try:
            detail = get_encounter_detail(selected)
        except KeyError as error:
            st.error(str(error), icon=":material/error:")
            return

        profile = detail["profile"]
        _render_patient_summary(profile)

        tabs = st.tabs(
            [
                ":material/timeline: 完整时间轴",
                ":material/person: 基本信息",
                ":material/diagnosis: 诊断信息",
                ":material/lab_profile: 检查与检验",
                ":material/medical_services: 治疗与病程",
                ":material/monitor_heart: 风险预测",
            ]
        )
        with tabs[0]:
            section_header("完整病情时间轴")
            _render_timeline(detail)
        with tabs[1]:
            section_header("基本信息")
            render_record_cards(detail["basic"], show_source=False)
        with tabs[2]:
            section_header("诊断信息")
            render_record_cards(
                detail["groups"]["诊断信息"],
                "该次就诊暂无诊断字段记录",
                show_source=False,
            )
        with tabs[3]:
            section_header("检查与检验")
            render_record_cards(
                detail["groups"]["检查与检验"],
                "该次就诊暂无检查或检验字段记录",
                show_source=False,
            )
        with tabs[4]:
            section_header("治疗与病程")
            _render_treatment_and_course(detail)
        with tabs[5]:
            section_header("风险预测")
            _render_risk(detail)
