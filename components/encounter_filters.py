from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st


def _clear_filter_state(prefix: str) -> None:
    suffixes = (
        "query",
        "age",
        "gender",
        "admission_dates",
        "discharge_dates",
        "diagnosis",
        "departments",
        "surgery",
    )
    for suffix in suffixes:
        st.session_state.pop(f"{prefix}_{suffix}", None)


def render_encounter_filters(
    frame: pd.DataFrame,
    *,
    prefix: str,
    include_discharge: bool = False,
    expanded: bool = True,
) -> pd.DataFrame:
    """Render a batched, workbook-backed encounter filter form."""
    if frame.empty:
        return frame

    ages = frame["age"].dropna().astype(int)
    minimum_age = int(ages.min()) if not ages.empty else 0
    maximum_age = int(ages.max()) if not ages.empty else 120
    gender_options = sorted(
        value for value in frame["gender"].dropna().unique().tolist() if value != "暂无记录"
    )
    department_options = sorted(
        value
        for value in frame["department"].dropna().unique().tolist()
        if value != "暂无记录"
    )

    with st.expander(
        "搜索与筛选",
        icon=":material/filter_list:",
        expanded=expanded,
    ):
        with st.form(f"{prefix}_filter_form", border=False):
            row1 = st.columns([1.35, 1, 1], vertical_alignment="bottom")
            query = row1[0].text_input(
                "患者编号或就诊编号",
                placeholder="输入 regno 或 admno",
                key=f"{prefix}_query",
            )
            age_range = row1[1].slider(
                "年龄范围",
                minimum_age,
                maximum_age,
                (minimum_age, maximum_age),
                key=f"{prefix}_age",
            )
            genders = row1[2].multiselect(
                "性别",
                gender_options,
                key=f"{prefix}_gender",
                placeholder="全部性别",
            )

            row2 = st.columns([1, 1, 1], vertical_alignment="bottom")
            admission_dates = row2[0].date_input(
                "入院 / 就诊日期",
                value=(),
                key=f"{prefix}_admission_dates",
                help="可选择起止日期；没有日期记录的就诊不会被纳入日期筛选结果。",
            )
            diagnosis = row2[1].text_input(
                "诊断名称",
                placeholder="输入诊断关键词",
                key=f"{prefix}_diagnosis",
            )
            departments = row2[2].multiselect(
                "诊断科室",
                department_options,
                key=f"{prefix}_departments",
                placeholder="全部科室",
            )

            row3 = st.columns([1, 1, 1], vertical_alignment="bottom")
            surgery = row3[0].text_input(
                "手术或介入名称",
                placeholder="输入手术关键词",
                key=f"{prefix}_surgery",
            )
            discharge_dates: tuple[date, ...] | date | list[date] = ()
            if include_discharge:
                discharge_dates = row3[1].date_input(
                    "出院日期",
                    value=(),
                    key=f"{prefix}_discharge_dates",
                )
            else:
                row3[1].caption("工作簿没有床位字段，页面未提供床位筛选。")
            with row3[2].container(horizontal=True, horizontal_alignment="right"):
                st.form_submit_button(
                    "清除",
                    icon=":material/restart_alt:",
                    on_click=_clear_filter_state,
                    args=(prefix,),
                )
                st.form_submit_button(
                    "应用筛选",
                    type="primary",
                    icon=":material/search:",
                )

        st.caption(
            "风险等级筛选未显示：当前工作簿没有可验证的模型风险分层字段。所有条件可组合使用。"
        )

    filtered = frame.copy()
    identifier_query = str(query or "").strip().lower()
    if identifier_query:
        filtered = filtered.loc[
            filtered["regno"].str.lower().str.contains(identifier_query, regex=False)
            | filtered["admno"].str.lower().str.contains(identifier_query, regex=False)
        ]
    filtered = filtered.loc[
        filtered["age"].isna()
        | filtered["age"].between(age_range[0], age_range[1], inclusive="both")
    ]
    if genders:
        filtered = filtered.loc[filtered["gender"].isin(genders)]
    if departments:
        filtered = filtered.loc[filtered["department"].isin(departments)]
    diagnosis_query = str(diagnosis or "").strip().lower()
    if diagnosis_query:
        filtered = filtered.loc[
            filtered["diagnosis"].str.lower().str.contains(diagnosis_query, regex=False)
        ]
    surgery_query = str(surgery or "").strip().lower()
    if surgery_query:
        filtered = filtered.loc[
            filtered["surgery"].str.lower().str.contains(surgery_query, regex=False)
        ]

    filtered = _filter_date_range(filtered, "admission_datetime", admission_dates)
    if include_discharge:
        filtered = _filter_date_range(filtered, "discharge_datetime", discharge_dates)
    return filtered.reset_index(drop=True)


def _filter_date_range(
    frame: pd.DataFrame,
    column: str,
    selected: tuple[date, ...] | date | list[date],
) -> pd.DataFrame:
    if not selected:
        return frame
    values = list(selected) if isinstance(selected, (tuple, list)) else [selected]
    if not values:
        return frame
    start = pd.Timestamp(values[0])
    end = pd.Timestamp(values[-1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    parsed = pd.to_datetime(frame[column], errors="coerce")
    return frame.loc[parsed.between(start, end, inclusive="both")]
