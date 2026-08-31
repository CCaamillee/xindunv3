from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components.cards import render_profile_strip, risk_badge
from components.header import render_header
from components.react_chat import (
    TOOL_LABELS,
    inject_react_chat_styles,
    partial_model_answer,
    partial_model_thinking,
    restored_timeline_steps,
    source_strip_html,
    timeline_html,
)
from components.patient_navigation import (
    consume_pending_encounter_key,
    navigate_to_patient_page,
    normalize_encounter_key,
)
from components.records import render_record_cards
from agent.react_agent import ClinicalReActAgent
from services.chat_store import (
    append_chat_message,
    clear_encounter_chat,
    load_encounter_chat,
)
from services.prediction_api import get_prediction_for_encounter, normalize_live_prediction
from services.workbook_data import get_encounter_dataframe, get_encounter_detail


SUGGESTIONS = {
    "核对诊断与主要病历": "请核对当前就诊中可确认的诊断和主要病历记录。",
    "梳理检查与检验": "请梳理当前就诊中已有的检查和检验项目，并说明不能可靠配对的部分。",
    "列出临床时间轴": "请列出当前就诊的结构化临床时间轴。",
    "运行心脏破裂预测": "请调用心脏破裂预测模型，判断患者未来14天是否发生心脏破裂及当前危急度，并列出核心依据。",
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


def _encounter_history(encounter_key: str) -> list[dict]:
    histories = st.session_state.setdefault("chat_history", {})
    if encounter_key not in histories:
        histories[encounter_key] = load_encounter_chat(encounter_key)
    return histories[encounter_key]


def _submit_question(encounter_key: str, question: str) -> None:
    question = str(question or "").strip()
    if not question:
        return
    pending = st.session_state.setdefault("pending_agent_questions", {})
    if encounter_key in pending:
        return
    history = _encounter_history(encounter_key)
    prior = list(history)
    message = {"role": "user", "content": question}
    history.append(message)
    append_chat_message(encounter_key, message)
    pending[encounter_key] = {"question": question, "history": prior}


def _clear_history(encounter_key: str) -> None:
    st.session_state.setdefault("chat_history", {})[encounter_key] = []
    st.session_state.setdefault("pending_agent_questions", {}).pop(encounter_key, None)
    clear_encounter_chat(encounter_key)


def _run_pending_question(encounter_key: str) -> None:
    pending_map = st.session_state.setdefault("pending_agent_questions", {})
    pending = pending_map.pop(encounter_key, None)
    if not pending:
        return

    history = _encounter_history(encounter_key)
    question = str(pending.get("question") or "").strip()
    prior_history = list(pending.get("history") or [])
    steps: list[dict] = []
    step_indexes: dict[tuple[object, ...], int] = {}
    risk_output = ""
    streamed_thinking = ""
    final_output = ""

    with st.chat_message("assistant", avatar=":material/clinical_notes:"):
        status_box = st.status("正在分析当前问题…", expanded=True)
        with status_box:
            process_slot = st.empty()
        final_slot = st.empty()

        def update_step(
            key: tuple[object, ...],
            title: str,
            *,
            detail: str | None = None,
            thinking: str | None = None,
            answer: str | None = None,
            fields: dict | None = None,
            status: str | None = None,
        ) -> None:
            if key not in step_indexes:
                step_indexes[key] = len(steps)
                steps.append({"title": title})
            step = steps[step_indexes[key]]
            step["title"] = title
            if detail is not None:
                step["detail"] = detail
            if thinking is not None:
                step["thinking"] = thinking
            if answer is not None:
                step["answer"] = answer
            if fields is not None:
                step["fields"] = fields
            if status is not None:
                step["status"] = status
            process_slot.markdown(timeline_html(steps), unsafe_allow_html=True)

        def tool_step_key(tool: str) -> tuple[object, ...]:
            return next(
                (key for key in reversed(step_indexes) if key[-1:] == (tool,)),
                ("tool", 0, tool),
            )

        def on_event(event: dict) -> None:
            nonlocal risk_output, streamed_thinking, final_output
            event_type = str(event.get("type") or "")
            if event_type == "phase":
                phase = str(event.get("phase") or "")
                iteration = event.get("iteration", 0)
                tool = str(event.get("tool") or "")
                if phase == "Reason":
                    update_step(
                        ("Reason", iteration),
                        str(event.get("title") or "明确查询内容"),
                        detail=str(event.get("detail") or "正在确定本次需要读取的资料。"),
                    )
                elif phase == "Act":
                    update_step(
                        ("tool", iteration, tool),
                        TOOL_LABELS.get(tool, "读取相关资料"),
                        detail=str(event.get("detail") or "正在读取并核对相关资料。"),
                    )
                elif phase == "Observation":
                    update_step(
                        ("tool", iteration, tool),
                        TOOL_LABELS.get(tool, "读取相关资料"),
                        detail=str(event.get("label") or "已取得所需资料。"),
                        status=str(event.get("status") or "success"),
                    )
                elif phase == "Final":
                    update_step(
                        ("Final",),
                        str(event.get("title") or "整理回答"),
                        detail=str(event.get("detail") or "正在整理已获得的信息。"),
                    )
            elif event_type == "risk_retry":
                update_step(
                    tool_step_key("calculate_risk"),
                    TOOL_LABELS["calculate_risk"],
                    detail="预测服务暂时未响应，正在尝试备用服务。",
                )
            elif event_type == "risk_think_delta":
                streamed_thinking += str(event.get("delta") or "")
                update_step(
                    tool_step_key("calculate_risk"),
                    TOOL_LABELS["calculate_risk"],
                    thinking=streamed_thinking.strip(),
                )
            elif event_type == "risk_delta":
                risk_output += str(event.get("delta") or "")
                thinking = partial_model_thinking(risk_output)
                answer = partial_model_answer(risk_output)
                if (thinking and not streamed_thinking) or answer:
                    update_step(
                        tool_step_key("calculate_risk"),
                        TOOL_LABELS["calculate_risk"],
                        thinking=thinking if not streamed_thinking else None,
                        answer=answer or None,
                    )
            elif event_type == "risk_complete":
                thinking = str(event.get("thinking") or "").strip()
                answer = str(event.get("answer") or "").strip()
                fields = event.get("fields") if isinstance(event.get("fields"), dict) else {}
                if thinking or answer:
                    update_step(
                        tool_step_key("calculate_risk"),
                        TOOL_LABELS["calculate_risk"],
                        thinking=thinking or None,
                        answer=answer or None,
                        fields=fields,
                    )
            elif event_type == "final_delta":
                final_output += str(event.get("delta") or "")
                if final_output.strip():
                    final_slot.markdown(final_output + " ▌")

        try:
            response = ClinicalReActAgent().run(
                encounter_key,
                question,
                history=prior_history,
                event_callback=on_event,
            )
        except Exception as exc:
            response = {
                "content": f"当前无法完成辅助诊断：{type(exc).__name__}",
                "sources": [],
                "trace": [],
                "mode": "react-unavailable",
                "reasoning": {"duration_seconds": 0, "trace": [], "risk_runs": []},
            }

        duration = float(response.get("reasoning", {}).get("duration_seconds") or 0)
        failed = response.get("mode") == "react-unavailable"
        status_box.update(
            label=(
                f"分析中断（用时 {duration:.1f} 秒）"
                if failed
                else f"已思考（用时 {duration:.1f} 秒）"
            ),
            state="error" if failed else "complete",
            expanded=False,
        )
        final_slot.markdown(str(response.get("content") or "当前没有可展示的回答。"))

    risk_runs = response.get("reasoning", {}).get("risk_runs", [])
    if risk_runs:
        st.session_state.setdefault("latest_model_predictions", {})[
            encounter_key
        ] = risk_runs[-1]
    message = {"role": "assistant", **response}
    history.append(message)
    append_chat_message(encounter_key, message)


def _render_reasoning(message: dict) -> None:
    steps = restored_timeline_steps(message)
    if not steps:
        return
    duration = float((message.get("reasoning") or {}).get("duration_seconds") or 0)
    with st.expander(f"已思考（用时 {duration:.1f} 秒）", expanded=False):
        st.markdown(timeline_html(steps), unsafe_allow_html=True)


def _render_message(message: dict) -> None:
    role = str(message.get("role") or "assistant")
    avatar = ":material/clinical_notes:" if role == "assistant" else None
    with st.chat_message(role, avatar=avatar):
        if role == "assistant":
            _render_reasoning(message)
        st.markdown(str(message.get("content") or "暂无回答"))
        if role == "assistant":
            source_html = source_strip_html(message.get("sources") or [])
            if source_html:
                st.markdown(source_html, unsafe_allow_html=True)


def _render_chat(encounter_key: str, profile: dict) -> None:
    inject_react_chat_styles()
    st.subheader(":material/clinical_notes: 辅助诊断记录")
    st.caption(
        f"当前就诊：{profile['regno']}｜{profile['admno']}。"
        "回答基于当前就诊资料；模型不可用时会明确说明。"
    )
    history = _encounter_history(encounter_key)
    if not history:
        with st.container(key="agent_empty_prompt", height="stretch", gap="small"):
            st.html(
                """
                <div class="agent-empty-state">
                  <span class="agent-empty-icon" aria-hidden="true">诊</span>
                  <strong>需要核对什么？</strong>
                  <span>选择下方常用问题，或在底部输入需要核对的内容。</span>
                </div>
                """
            )
            suggestion_columns = st.columns(2, gap="small")
            for index, (label, suggestion) in enumerate(SUGGESTIONS.items()):
                if suggestion_columns[index % 2].button(
                    label,
                    icon=":material/arrow_outward:",
                    width="stretch",
                    key=f"agent_suggestion_{index}_{encounter_key}",
                ):
                    _submit_question(encounter_key, suggestion)
                    st.rerun()
    else:
        for message in history:
            _render_message(message)

    if encounter_key in st.session_state.setdefault("pending_agent_questions", {}):
        _run_pending_question(encounter_key)

    if history and st.button(
        "清空当前就诊问答",
        icon=":material/delete_sweep:",
        key=f"clear_chat_{encounter_key}",
        on_click=_clear_history,
        args=(encounter_key,),
    ):
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
                                f"<div class='risk-legend'><strong>模型预测</strong>"
                                f"{risk_badge(normalized['risk_level'])}"
                                f"<span>破裂判断：{normalized['rupture_judgment']}｜"
                                f"当前危急度：{normalized['current_urgency'] or '模型未提供'}</span></div>",
                                unsafe_allow_html=True,
                            )
                            if normalized["core_evidence"]:
                                st.caption(normalized["core_evidence"])
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
