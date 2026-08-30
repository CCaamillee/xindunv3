from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components.cards import render_profile_strip, risk_badge
from components.header import render_header
from components.patient_navigation import (
    consume_pending_encounter_key,
    navigate_to_patient_page,
    normalize_encounter_key,
)
from components.records import render_record_cards
from agent.react_agent import ClinicalReActAgent
from services.prediction_api import get_prediction_for_encounter, normalize_live_prediction
from services.workbook_data import get_encounter_dataframe, get_encounter_detail


SUGGESTIONS = {
    "核对诊断与主要病历": "请核对当前就诊中可确认的诊断和主要病历记录。",
    "梳理检查与检验": "请梳理当前就诊中已有的检查和检验项目，并说明不能可靠配对的部分。",
    "列出临床时间轴": "请列出当前就诊的结构化临床时间轴。",
    "运行心脏破裂预测": "请调用心脏破裂二分类模型，判断当前就诊资料对应的患者是否会发生心脏破裂，并列出模型依据。模型未提供概率或具体发生时间时请明确说明。",
}


def _clear_filters() -> None:
    for key in (
        "agent_query",
        "agent_age",
        "agent_gender",
        "agent_admission_dates",
        "agent_diagnosis",
        "agent_department",
        "agent_surgery",
    ):
        st.session_state.pop(key, None)


def _filter_frame(frame: pd.DataFrame, values: dict) -> pd.DataFrame:
    filtered = frame.copy()
    query = str(values.get("query") or "").strip().lower()
    if query:
        filtered = filtered.loc[
            filtered["regno"].str.lower().str.contains(query, regex=False)
            | filtered["admno"].str.lower().str.contains(query, regex=False)
        ]
    age_range = values["age"]
    filtered = filtered.loc[
        filtered["age"].isna()
        | filtered["age"].between(age_range[0], age_range[1], inclusive="both")
    ]
    if values["gender"]:
        filtered = filtered.loc[filtered["gender"].isin(values["gender"])]
    if values["department"]:
        filtered = filtered.loc[filtered["department"].isin(values["department"])]
    for field, column in (("diagnosis", "diagnosis"), ("surgery", "surgery")):
        text = str(values.get(field) or "").strip().lower()
        if text:
            filtered = filtered.loc[
                filtered[column].str.lower().str.contains(text, regex=False)
            ]
    selected_dates = values.get("admission_dates")
    if selected_dates:
        dates = list(selected_dates) if isinstance(selected_dates, (tuple, list)) else [selected_dates]
        start = pd.Timestamp(dates[0])
        end = pd.Timestamp(dates[-1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        parsed = pd.to_datetime(filtered["admission_datetime"], errors="coerce")
        filtered = filtered.loc[parsed.between(start, end, inclusive="both")]
    return filtered.reset_index(drop=True)


def _render_filter_panel(frame: pd.DataFrame) -> pd.DataFrame:
    ages = frame["age"].dropna().astype(int)
    min_age = int(ages.min()) if not ages.empty else 0
    max_age = int(ages.max()) if not ages.empty else 120
    genders = sorted(value for value in frame["gender"].unique() if value != "暂无记录")
    departments = sorted(value for value in frame["department"].unique() if value != "暂无记录")

    with st.form("agent_filter_form", border=False):
        query = st.text_input(
            "按编号查找患者",
            placeholder="输入 regno 或 admno",
            key="agent_query",
        )
        with st.expander(
            "更多患者信息组合查找",
            icon=":material/tune:",
        ):
            age_range = st.slider(
                "年龄范围",
                min_age,
                max_age,
                (min_age, max_age),
                key="agent_age",
            )
            gender = st.multiselect(
                "性别",
                genders,
                key="agent_gender",
                placeholder="全部性别",
            )
            admission_dates: tuple[date, ...] | date | list[date] = st.date_input(
                "入院 / 就诊日期",
                value=(),
                key="agent_admission_dates",
            )
            diagnosis = st.text_input(
                "诊断名称",
                placeholder="输入诊断关键词",
                key="agent_diagnosis",
            )
            department = st.multiselect(
                "诊断科室",
                departments,
                key="agent_department",
                placeholder="全部科室",
            )
            surgery = st.text_input(
                "手术或介入名称",
                placeholder="输入手术关键词",
                key="agent_surgery",
            )
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.form_submit_button(
                "清除",
                icon=":material/restart_alt:",
                on_click=_clear_filters,
            )
            st.form_submit_button(
                "应用筛选",
                type="primary",
                icon=":material/search:",
            )

    st.caption("可按编号直接定位，也可展开年龄、性别、日期、诊断、科室和手术条件组合查找。")
    return _filter_frame(
        frame,
        {
            "query": query,
            "age": age_range,
            "gender": gender,
            "admission_dates": admission_dates,
            "diagnosis": diagnosis,
            "department": department,
            "surgery": surgery,
        },
    )


def _encounter_label(key: str, encounter_map: dict[str, dict]) -> str:
    item = encounter_map[key]
    diagnosis = item["diagnosis"]
    if len(diagnosis) > 34:
        diagnosis = diagnosis[:34] + "…"
    return f"{item['regno']}｜{item['admno']}｜{item['age'] or '暂无年龄'}岁｜{diagnosis}"


def _open_detail(encounter_key: str) -> None:
    navigate_to_patient_page("病情详情", encounter_key)


def _submit_question(encounter_key: str, question: str) -> None:
    history_by_scope = st.session_state.setdefault("chat_history", {})
    history = history_by_scope.setdefault(encounter_key, [])
    prior = list(history)
    history.append({"role": "user", "content": question})
    events: list[dict] = []
    with st.spinner("正在核对当前就诊资料并调用所需模型…"):
        response = ClinicalReActAgent().run(
            encounter_key,
            question,
            history=prior,
            event_callback=events.append,
        )
    response["events"] = events
    risk_runs = response.get("reasoning", {}).get("risk_runs", [])
    if risk_runs:
        st.session_state.setdefault("latest_model_predictions", {})[
            encounter_key
        ] = risk_runs[-1]
    history.append({"role": "assistant", **response})


def _render_chat(encounter_key: str, profile: dict) -> None:
    st.subheader(":material/clinical_notes: Agent 辅助问答")
    st.caption(
        f"当前就诊：{profile['regno']}｜{profile['admno']}。"
        "回答仅核对当前就诊的工作簿资料；模型不可用时会明确说明。"
    )
    history = st.session_state.setdefault("chat_history", {}).setdefault(encounter_key, [])
    if not history:
        with st.container(
            key="agent_empty_prompt",
            height="stretch",
            gap="small",
        ):
            st.html(
                """
                <div class="agent-empty-state">
                  <span class="agent-empty-icon" aria-hidden="true">✚</span>
                  <strong>需要核对什么？</strong>
                  <span>选择下方常用问题，或在底部输入需要核对的内容。</span>
                </div>
                """
            )
            suggestion_columns = st.columns(2, gap="small")
            selected_question = None
            for index, (label, question) in enumerate(SUGGESTIONS.items()):
                if suggestion_columns[index % 2].button(
                    label,
                    icon=":material/arrow_outward:",
                    width="stretch",
                    key=f"agent_suggestion_{index}_{encounter_key}",
                ):
                    selected_question = question
        if selected_question:
            _submit_question(encounter_key, selected_question)
            st.rerun()
    else:
        for message in history:
            role = message.get("role", "assistant")
            with st.chat_message(role, avatar=":material/clinical_notes:" if role == "assistant" else None):
                st.write(message.get("content") or "暂无回答")
                if role == "assistant" and message.get("sources"):
                    st.caption("来源：" + "｜".join(str(item) for item in message["sources"]))
                if role == "assistant" and message.get("trace"):
                    with st.expander("查看资料核对过程", icon=":material/fact_check:"):
                        for step in message["trace"]:
                            status = "完成" if step.get("status") == "success" else "未完成"
                            st.markdown(
                                f"**{step.get('tool', '资料工具')} · {status}**  "
                                f"{step.get('observation', '暂无过程摘要')}"
                            )
        if st.button(
            "清空当前就诊问答",
            icon=":material/delete_sweep:",
            key=f"clear_chat_{encounter_key}",
        ):
            st.session_state["chat_history"][encounter_key] = []
            st.rerun()

    question = st.chat_input(
        "输入需要核对的问题…",
        key=f"agent_chat_input_{encounter_key}",
        submit_mode="disable",
    )
    if question:
        _submit_question(encounter_key, question)
        st.rerun()


def render() -> None:
    with st.container(key="clinical_agent_page", gap="small"):
        render_header(
            "辅助诊断",
            "选择患者并核对一次就诊资料，使用 Agent 辅助梳理诊断、检查检验、治疗与临床时间线。",
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
        if pending in set(frame["encounter_key"]):
            st.session_state.selected_encounter_key = pending
            st.session_state.agent_encounter_selector = pending

        patient_col, chat_col = st.columns(
            [0.95, 2.05],
            gap="medium",
            vertical_alignment="top",
        )
        with patient_col:
            with st.container(
                border=True,
                height="stretch",
                key="agent_patient_panel",
            ):
                st.subheader(":material/person_search: 患者与就诊")
                st.caption("按编号快速定位，或展开更多条件组合查找。")
                filtered = _render_filter_panel(frame)
                if filtered.empty:
                    st.warning(
                        "没有符合筛选条件的就诊记录，请调整搜索条件。",
                        icon=":material/search_off:",
                    )
                    return

                encounter_map = {
                    row["encounter_key"]: row.to_dict()
                    for _, row in filtered.iterrows()
                }
                options = list(encounter_map)
                selected_state = normalize_encounter_key(
                    st.session_state.get("selected_encounter_key")
                )
                if selected_state not in encounter_map:
                    selected_state = options[0]
                if st.session_state.get("agent_encounter_selector") not in encounter_map:
                    st.session_state.agent_encounter_selector = selected_state
                selected = st.selectbox(
                    "当前患者与就诊",
                    options,
                    format_func=lambda value: _encounter_label(value, encounter_map),
                    key="agent_encounter_selector",
                    persist_state="session",
                )
                st.session_state.selected_encounter_key = selected
                st.caption(f"筛选后 {len(filtered)} 条就诊记录")

                detail = get_encounter_detail(selected)
                profile = detail["profile"]
                st.subheader(":material/id_card: 当前患者")
                render_profile_strip(profile)
                st.button(
                    "查看病情详情",
                    icon=":material/timeline:",
                    type="primary",
                    width="stretch",
                    on_click=_open_detail,
                    args=(selected,),
                    key=f"agent_to_detail_{selected}",
                )

                with st.expander(
                    "展开结构化患者资料",
                    icon=":material/folder_open:",
                ):
                    record_group = st.selectbox(
                        "资料分组",
                        ["概览", "诊断", "检查与检验", "治疗与病程", "风险信息"],
                        key=f"agent_record_group_{selected}",
                    )
                    if record_group == "概览":
                        render_record_cards(detail["basic"])
                    elif record_group == "诊断":
                        render_record_cards(
                            detail["groups"]["诊断信息"],
                            "暂无诊断字段记录",
                        )
                    elif record_group == "检查与检验":
                        render_record_cards(
                            detail["groups"]["检查与检验"],
                            "暂无检查或检验字段记录",
                        )
                    elif record_group == "治疗与病程":
                        treatment = [
                            *detail["groups"]["用药与医嘱"],
                            *detail["groups"]["手术信息"],
                            *detail["groups"]["病程记录"],
                        ]
                        render_record_cards(treatment, "暂无治疗或病程字段记录")
                    else:
                        live_result = st.session_state.get(
                            "latest_model_predictions", {}
                        ).get(selected) or get_prediction_for_encounter(selected)
                        normalized = normalize_live_prediction(live_result or {})
                        if normalized["available"]:
                            st.markdown(
                                f"<div class='risk-legend'><strong>模型风险状态</strong>"
                                f"{risk_badge(normalized['risk_level'])}"
                                f"<span>预测时间窗：{normalized['prediction_time']}｜"
                                f"证据支持度：{normalized['evidence_confidence']}</span></div>",
                                unsafe_allow_html=True,
                            )
                            st.caption(normalized["notice"])
                        else:
                            st.markdown(
                                f"<div class='risk-legend'><strong>风险状态</strong>"
                                f"{risk_badge(profile['risk_level'])}"
                                "<span>尚无本次就诊的有效预测模型结果。</span></div>",
                                unsafe_allow_html=True,
                            )
                        render_record_cards(detail["risk"])

        with chat_col:
            with st.container(
                border=True,
                height="stretch",
                key="agent_chat_panel",
            ):
                _render_chat(selected, profile)
