from __future__ import annotations

import html
from datetime import datetime

import pandas as pd
import streamlit as st

from components.cards import risk_badge, status_badge
from components.header import render_header, section_header
from services.data_api import get_alerts, get_tasks


def render() -> None:
    render_header("重点患者")
    alerts = get_alerts(limit=40)
    overrides = st.session_state.setdefault("alert_overrides", {})
    audit = st.session_state.setdefault("audit_log", [])
    for alert in alerts:
        if alert["alert_id"] in overrides:
            alert["status"] = overrides[alert["alert_id"]]

    cols = st.columns(4)
    cols[0].metric("待复核", sum(row["status"] == "待复核" for row in alerts))
    cols[1].metric("处理中", sum(row["status"] == "处理中" for row in alerts))
    cols[2].metric("已复核", sum(row["status"] == "已复核" for row in alerts))
    cols[3].metric("本次会话误报反馈", sum(row["status"] == "已标记误报" for row in alerts))

    left, right = st.columns([1.6, 1])
    with left:
        section_header("回顾性重点记录")
        status = st.selectbox("状态", ["全部"] + sorted({row["status"] for row in alerts}))
        filtered = [row for row in alerts if status == "全部" or row["status"] == status]
        table = pd.DataFrame(filtered)
        if not table.empty:
            table = table.rename(
                columns={"time": "数据窗口", "patient_id": "展示编号", "level": "优先级", "reason": "依据", "status": "状态", "owner": "负责人"}
            )[["数据窗口", "展示编号", "优先级", "依据", "状态", "负责人"]]
            st.dataframe(table, hide_index=True, width="stretch", height=390)
        else:
            st.markdown("<div class='empty-state'>当前筛选条件下暂无记录</div>", unsafe_allow_html=True)
    with right:
        section_header("复核详情")
        selected_id = st.selectbox("选择记录", [row["alert_id"] for row in alerts])
        selected = next(row for row in alerts if row["alert_id"] == selected_id)
        st.markdown(
            f"<div class='alert-box'><div class='xd-card-title'>{selected['alert_id']}</div>"
            f"<div style='margin:.5rem 0'>{risk_badge(selected['level'])} {status_badge(selected['status'])}</div>"
            f"<div class='xd-card-sub'>{html.escape(selected['patient_id'])} · {html.escape(selected['source'])}</div>"
            f"<div style='font-size:12px;margin-top:.65rem;color:#334E68'>{html.escape(selected['reason'])}</div></div>",
            unsafe_allow_html=True,
        )
        st.markdown("#### 复核清单")
        st.checkbox("确认队列标签与纳排标准", key=f"check_label_{selected_id}")
        st.checkbox("复核关键生命体征、超声与检验字段", key=f"check_feature_{selected_id}")
        st.checkbox("确认缺失数据和结论边界", key=f"check_gap_{selected_id}")
        a, b, c = st.columns(3)
        for column, label, new_status in [(a, "确认", "已复核"), (b, "处理中", "处理中"), (c, "误报", "已标记误报")]:
            if column.button(label, key=f"act_{label}_{selected_id}", width="stretch"):
                overrides[selected_id] = new_status
                audit.insert(0, {"time": datetime.now().strftime("%H:%M:%S"), "action": label, "target": selected_id, "operator": "当前用户"})
                st.rerun()
        if st.button("进入患者评估", type="primary", width="stretch"):
            st.session_state.selected_patient_id = selected["patient_id"]
            st.session_state.pending_page = "患者评估"
            st.rerun()

    task_col, audit_col = st.columns(2)
    with task_col:
        section_header("复核任务草稿")
        tasks = pd.DataFrame(get_tasks(limit=16)).rename(
            columns={"title": "任务", "patient_id": "展示编号", "priority": "优先级", "due": "截止", "owner": "负责人", "status": "状态"}
        )
        st.dataframe(tasks[["任务", "展示编号", "优先级", "截止", "负责人", "状态"]], hide_index=True, width="stretch", height=260)
    with audit_col:
        section_header("操作审计")
        if audit:
            st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch", height=260)
        else:
            st.markdown("<div class='empty-state'>尚无本次会话操作记录</div>", unsafe_allow_html=True)
    st.markdown("<div class='safe-note'>此页是回顾性数据复核工作流，不是已上线的实时临床告警系统。</div>", unsafe_allow_html=True)

