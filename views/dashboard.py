from __future__ import annotations

import html
import math

import pandas as pd
import streamlit as st

from components.encounter_filters import render_encounter_filters
from components.header import render_header, section_header
from components.patient_navigation import navigate_to_patient_page
from services.model_runtime import (
    check_risk_model_health,
    launch_batch_prediction,
    load_batch_status,
)
from services.prediction_api import (
    build_live_prediction_overview,
    get_prediction_overview,
    get_prediction_records_by_encounter,
    normalize_live_prediction,
)
from services.workbook_data import get_encounter_dataframe, get_metrics


ROWS_PER_PAGE = 12


def _render_metrics(metrics: dict, prediction_overview: dict) -> None:
    with st.container(horizontal=True):
        st.metric(
            "患者总数",
            metrics["patient_count"],
            "按 regno 去重",
            delta_color="off",
            delta_arrow="off",
            icon=":material/groups:",
            border=True,
        )
        st.metric(
            "就诊记录数",
            metrics["encounter_count"],
            "按 regno + admno 区分",
            delta_color="blue",
            delta_arrow="off",
            icon=":material/assignment:",
            border=True,
        )
        st.metric(
            "多次就诊患者",
            metrics["multi_visit_patient_count"],
            "存在多个 admno",
            delta_color="orange",
            delta_arrow="off",
            icon=":material/repeat:",
            border=True,
        )
        st.metric(
            "目标事件记录",
            metrics["target_event_count"],
            "工作簿 label=1",
            delta_color="red",
            delta_arrow="off",
            icon=":material/notification_important:",
            border=True,
        )
        st.metric(
            "手术 / 介入有记录",
            metrics["surgery_record_count"],
            "仅统计工作簿非空字段",
            delta_color="green",
            delta_arrow="off",
            icon=":material/medical_services:",
            border=True,
        )
        if prediction_overview.get("available"):
            st.metric(
                "有效模型结果",
                prediction_overview["total"],
                f"{prediction_overview['scope']} · 总体研判",
                delta_color="blue",
                delta_arrow="off",
                icon=":material/model_training:",
                border=True,
            )
        else:
            st.metric(
                "模型风险分层",
                "暂无",
                "未发现可验证模型结果",
                delta_color="gray",
                delta_arrow="off",
                icon=":material/help:",
                border=True,
            )


def _distribution_counts(rows: list[dict]) -> dict[str, int]:
    return {str(row.get("key")): int(row.get("count", 0)) for row in rows}


def _render_prediction_focus(prediction_overview: dict) -> None:
    section_header("模型研判重点")
    if not prediction_overview.get("available"):
        reason = html.escape(str(prediction_overview.get("reason", "暂无可验证模型结果。")))
        st.html(
            f"""
            <section class="model-focus-unavailable" role="status" aria-label="模型结果状态">
              <span class="material-symbols-rounded" aria-hidden="true">model_training</span>
              <div>
                <strong>暂未接入可验证的模型结果</strong>
                <p>{reason} 当前页面不会使用工作簿中的回顾性 label 或 cutoff_time 推导预测风险。</p>
              </div>
            </section>
            """
        )
        return

    risk_counts = _distribution_counts(prediction_overview.get("risk_distribution", []))
    review_count = int(prediction_overview.get("review_count", 0))
    positive_count = int(prediction_overview.get("predicted_positive_count", 0))
    total = int(prediction_overview.get("total", 0))
    review_rate = float(prediction_overview.get("review_rate", 0.0))
    positive_rate = float(prediction_overview.get("predicted_positive_rate", 0.0))
    source_file = html.escape(str(prediction_overview.get("source_file", "暂无记录")))
    scope = html.escape(str(prediction_overview.get("scope", "模型结果")))
    risk_rule = html.escape(str(prediction_overview.get("risk_rule", "")))
    review_rule = html.escape(str(prediction_overview.get("review_rule", "")))

    window_rows = prediction_overview.get("time_window_distribution", [])
    if window_rows:
        window_html = "".join(
            (
                '<span class="model-window-chip">'
                f'<span>{html.escape(str(row.get("label", "暂无记录")))}</span>'
                f'<strong>{int(row.get("count", 0))} 条</strong>'
                "</span>"
            )
            for row in window_rows
        )
    else:
        window_html = '<span class="model-window-empty">暂无预测发生时间窗记录</span>'

    st.html(
        f"""
        <section class="model-focus-panel" aria-labelledby="model-focus-title">
          <div class="model-focus-heading">
            <div>
              <span class="model-focus-kicker">MODEL PRIORITY OVERVIEW</span>
              <h3 id="model-focus-title">按研判优先级突出需要关注的数据</h3>
              <p>以下为当前工作簿真实模型结果的总体分布；带 encounter_key 的结果会同步回填患者列表。</p>
            </div>
            <span class="model-focus-scope">
              <span class="material-symbols-rounded" aria-hidden="true">verified</span>{scope}
            </span>
          </div>

          <div class="model-focus-grid">
            <article class="model-focus-card model-focus-card--review">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">priority_high</span>
                <span>最高优先级</span>
              </div>
              <strong class="model-focus-value">{review_count}</strong>
              <h4>建议重点复核</h4>
              <p>占有效结果 {review_rate:.1%}，需优先结合病历证据进行人工核对。</p>
            </article>

            <article class="model-focus-card model-focus-card--positive">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">notification_important</span>
                <span>预测阳性</span>
              </div>
              <strong class="model-focus-value">{positive_count}</strong>
              <h4>模型预测会发生</h4>
              <p>占有效结果 {positive_rate:.1%}，来源为模型二分类 predicted_label=1。</p>
            </article>

            <article class="model-focus-card model-focus-card--total">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">dataset</span>
                <span>模型覆盖</span>
              </div>
              <strong class="model-focus-value">{total}</strong>
              <h4>有效模型结果</h4>
              <p>仅统计解析成功且预测时间窗有效的记录。</p>
            </article>

            <article class="model-focus-card model-focus-card--high">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">emergency_home</span>
                <span>高风险</span>
              </div>
              <strong class="model-focus-value">{risk_counts.get('HIGH', 0)}</strong>
              <h4>高风险模型结果</h4>
              <p>模型二分类结论为“会发生”，按透明界面规则列为高优先级复核。</p>
            </article>

            <article class="model-focus-card model-focus-card--medium">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">warning</span>
                <span>中风险</span>
              </div>
              <strong class="model-focus-value">{risk_counts.get('MEDIUM', 0)}</strong>
              <h4>中风险模型结果</h4>
              <p>仅在模型返回结构化低证据支持度时使用，需要进一步人工复核。</p>
            </article>

            <article class="model-focus-card model-focus-card--low">
              <div class="model-focus-card-head">
                <span class="material-symbols-rounded" aria-hidden="true">check_circle</span>
                <span>低风险</span>
              </div>
              <strong class="model-focus-value">{risk_counts.get('LOW', 0)}</strong>
              <h4>低风险</h4>
              <p>模型二分类结论为“未发生”，不代表风险为零。</p>
            </article>
          </div>

          <div class="model-window-row" aria-label="预测发生时间窗分布">
            <strong>预测发生时间窗</strong>
            <div>{window_html}</div>
          </div>
          <div class="model-focus-note">
            <span class="material-symbols-rounded" aria-hidden="true">clinical_notes</span>
            <div><strong>研判边界：</strong>{review_rule} {risk_rule} 结果仅用于辅助复核，不能替代医生诊断。</div>
          </div>
          <p class="model-focus-source">模型结果来源：{source_file}｜统计范围：{scope}</p>
        </section>
        """
    )


def _render_model_runtime(encounter_count: int, persisted_count: int) -> None:
    section_header("预测模型运行")
    health = check_risk_model_health()
    status = load_batch_status()
    with st.container(border=True, key="prediction_runtime_panel"):
        service_col, result_col, progress_col = st.columns(
            [1.3, 1, 1],
            gap="medium",
            vertical_alignment="center",
        )
        service_col.metric(
            "推理服务",
            "在线" if health.get("available") else "未启动",
            health.get("model", "cardiac-rupture-qwen38"),
            delta_color="green" if health.get("available") else "gray",
            delta_arrow="off",
            icon=":material/memory:",
            border=True,
        )
        result_col.metric(
            "已保存预测",
            persisted_count,
            f"当前工作簿共 {encounter_count} 条就诊",
            delta_color="blue",
            delta_arrow="off",
            icon=":material/database:",
            border=True,
        )
        progress_col.metric(
            "批量任务",
            {
                "idle": "未启动",
                "starting": "启动中",
                "running": "运行中",
                "completed": "已完成",
                "failed": "已停止",
                "error": "状态异常",
            }.get(str(status.get("state")), "未知"),
            str(status.get("updated_at") or "暂无运行时间"),
            delta_color="blue",
            delta_arrow="off",
            icon=":material/batch_prediction:",
            border=True,
        )

        if health.get("available"):
            st.success(health["message"], icon=":material/check_circle:")
        else:
            st.warning(
                health["message"]
                + " 当前电脑不会用 label=0 代替模型预测；需先在具备足够显存的服务器启动模型服务。",
                icon=":material/power_settings_new:",
            )
            with st.expander("查看服务器启动与连接说明", icon=":material/terminal:"):
                st.code(
                    "python -m vllm.entrypoints.openai.api_server "
                    "--model /path/to/xinzangpolie/model "
                    "--served-model-name cardiac-rupture-qwen38 "
                    "--tensor-parallel-size 2 --dtype bfloat16 "
                    "--max-model-len 8192 --port 8000",
                    language="bash",
                )
                st.caption(
                    "模型服务与网页不在同一台机器时，请把 CARDIAC_RISK_URLS 设置为网页服务器可访问的内网地址；"
                    "模型训练评估脚本采用双卡 vLLM，本机 4GB 显存不适合直接加载约15GB权重。"
                )

        total = int(status.get("total") or 0)
        completed = int(status.get("completed") or 0)
        failed = int(status.get("failed") or 0)
        if total:
            st.progress(
                min(1.0, (completed + failed) / total),
                text=f"任务进度：完成 {completed} / {total}，失败 {failed}",
            )
        st.caption(str(status.get("message") or "尚无批量预测状态。"))

        scope_options = {
            "先预测100条（连通性验证）": 100,
            "预测前1000条": 1000,
            f"预测全部 {encounter_count} 条就诊": None,
        }
        action_col, refresh_col = st.columns([2.4, 1], vertical_alignment="bottom")
        selected_scope = action_col.selectbox(
            "批量预测范围",
            list(scope_options),
            key="prediction_batch_scope",
            disabled=not health.get("available"),
        )
        with action_col:
            if st.button(
                "启动 / 续跑批量预测",
                icon=":material/play_arrow:",
                type="primary",
                width="stretch",
                disabled=(
                    not health.get("available")
                    or status.get("state") in {"starting", "running"}
                ),
                key="start_prediction_batch",
            ):
                outcome = launch_batch_prediction(scope_options[selected_scope])
                if outcome.get("started"):
                    st.success(outcome["message"])
                else:
                    st.error(outcome["message"])
                st.rerun()
        with refresh_col:
            if st.button(
                "刷新连接与进度",
                icon=":material/refresh:",
                width="stretch",
                key="refresh_prediction_runtime",
            ):
                check_risk_model_health.clear()
                st.rerun()


def _render_legend(has_live_results: bool = False) -> None:
    explanation = (
        "带颜色的记录来自本次会话中真实完成的预测模型调用；其余记录仍以灰色“无法判断”呈现。"
        if has_live_results
        else "当前工作簿没有模型风险与预测时间字段，因此尚未运行模型的记录均以灰色“无法判断”呈现。"
    )
    st.markdown(
        f"""
        <div class="risk-legend" role="note" aria-label="风险等级颜色图例">
          <strong>风险图例</strong>
          <span class="risk-badge risk-HIGH">高风险</span>
          <span class="risk-badge risk-MEDIUM">中风险</span>
          <span class="risk-badge risk-LOW">低风险</span>
          <span class="risk-badge risk-UNKNOWN">无法判断</span>
          <span>{explanation}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _sort_frame(frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    sorted_frame = frame.copy()
    if mode == "入院时间（新到旧）":
        return sorted_frame.sort_values("admission_datetime", ascending=False, na_position="last")
    if mode == "年龄（高到低）":
        return sorted_frame.sort_values(["age", "admission_datetime"], ascending=[False, False], na_position="last")
    if mode == "患者编号":
        return sorted_frame.sort_values(["regno", "admno"], ascending=True)
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    sorted_frame["_risk_rank"] = sorted_frame["risk_level"].map(rank).fillna(3)
    return sorted_frame.sort_values(
        ["_risk_rank", "admission_datetime", "cutoff_datetime"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns="_risk_rank")


def _display_table(page_frame: pd.DataFrame) -> None:
    icons = {"高风险": "🔴", "中风险": "🟠", "低风险": "🟢", "无法判断": "⚪"}
    display = pd.DataFrame(
        {
            "患者编号": page_frame["regno"],
            "就诊编号": page_frame["admno"],
            "年龄": page_frame["age"].astype("Int64"),
            "性别": page_frame["gender"],
            "入院 / 就诊时间": page_frame["admission_time"],
            "主要诊断": page_frame["diagnosis"],
            "风险等级": page_frame["risk_label"].map(
                lambda value: f"{icons.get(value, '⚪')} {value}"
            ),
            "预测 / 可能发生时间": page_frame["prediction_time"],
            "当前状态 / 结局": page_frame["outcome"],
            "数据窗口截止": page_frame["cutoff_time"],
        }
    ).reset_index(drop=True)
    def risk_row_style(row: pd.Series) -> list[str]:
        risk_text = str(row["风险等级"])
        if "高风险" in risk_text:
            style = "color:#9F1D20;font-weight:700;background-color:#FDEBEC"
        elif "中风险" in risk_text:
            style = "color:#8A4B08;font-weight:700;background-color:#FFF3DC"
        elif "低风险" in risk_text:
            style = "color:#17613A;font-weight:700;background-color:#EAF7EF"
        else:
            style = "color:#66788A;font-weight:600;background-color:#F1F4F6"
        return [style if column in {"风险等级", "预测 / 可能发生时间"} else "" for column in row.index]

    styled = display.style.apply(risk_row_style, axis=1)
    event = st.dataframe(
        styled,
        hide_index=True,
        height="content",
        width="stretch",
        key="dashboard_patient_table",
        on_select="rerun",
        selection_mode="single-row",
        placeholder="暂无记录",
        row_height=42,
        column_config={
            "患者编号": st.column_config.TextColumn("患者编号", pinned=True, width="medium"),
            "就诊编号": st.column_config.TextColumn("就诊编号", pinned=True, width="medium"),
            "年龄": st.column_config.NumberColumn("年龄", format="%d 岁", width="small"),
            "性别": st.column_config.TextColumn("性别", width="small"),
            "入院 / 就诊时间": st.column_config.TextColumn("入院 / 就诊时间", width="medium"),
            "主要诊断": st.column_config.TextColumn("主要诊断", width="large"),
            "风险等级": st.column_config.TextColumn("风险等级", width="medium"),
            "预测 / 可能发生时间": st.column_config.TextColumn("预测 / 可能发生时间", width="medium"),
            "当前状态 / 结局": st.column_config.TextColumn("当前状态 / 结局", width="large"),
            "数据窗口截止": st.column_config.TextColumn("数据窗口截止", width="medium"),
        },
    )
    if event.selection.rows:
        row_index = int(event.selection.rows[0])
        encounter_key = page_frame.iloc[row_index]["encounter_key"]
        navigate_to_patient_page("病情详情", encounter_key)
        st.rerun()


def render() -> None:
    render_header(
        "急诊概览",
        "汇总上传工作簿中的真实患者与就诊记录。选择表格任意行可直接打开对应患者、对应就诊的病情详情。",
    )
    try:
        with st.skeleton(height=130):
            metrics = get_metrics()
            frame = get_encounter_dataframe()
            prediction_overview = get_prediction_overview()
    except (FileNotFoundError, ImportError, ValueError) as error:
        st.error(f"患者工作簿加载失败：{error}", icon=":material/error:")
        return

    persisted_predictions = get_prediction_records_by_encounter()
    live_predictions = st.session_state.get("latest_model_predictions", {})
    all_predictions = {**persisted_predictions, **live_predictions}
    valid_live_predictions: list[dict] = []
    frame = frame.copy()
    for index, row in frame.iterrows():
        result = all_predictions.get(row["encounter_key"])
        normalized = normalize_live_prediction(result or {})
        if not normalized["available"]:
            continue
        valid_live_predictions.append(result)
        frame.at[index, "risk_level"] = normalized["risk_level"]
        frame.at[index, "risk_label"] = normalized["risk_label"]
        frame.at[index, "prediction_time"] = normalized["prediction_time"]
    if valid_live_predictions and not prediction_overview.get("available"):
        prediction_overview = build_live_prediction_overview(valid_live_predictions)

    _render_metrics(metrics, prediction_overview)
    st.caption(
        f"数据来源：{metrics['source_file']}｜运行时 Excel 只读｜当前统计单位同时区分患者（regno）与就诊（admno）"
    )
    _render_model_runtime(metrics["encounter_count"], len(persisted_predictions))
    _render_prediction_focus(prediction_overview)

    section_header("患者基本情况")
    _render_legend(bool(valid_live_predictions))
    filtered = render_encounter_filters(frame, prefix="dashboard", expanded=False)
    if filtered.empty:
        st.warning("没有符合当前组合条件的就诊记录，请调整或清除筛选条件。", icon=":material/search_off:")
        return

    sort_col, count_col = st.columns([1, 2], vertical_alignment="bottom")
    sort_mode = sort_col.selectbox(
        "排序方式",
        ["风险等级优先", "入院时间（新到旧）", "年龄（高到低）", "患者编号"],
        key="dashboard_sort_mode",
    )
    count_col.caption(
        f"筛选后 {len(filtered)} 条就诊记录。风险等级均无法判断时，“风险等级优先”会按入院时间与窗口截止时间排序。"
    )
    sorted_frame = _sort_frame(filtered, sort_mode).reset_index(drop=True)
    total_pages = max(1, math.ceil(len(sorted_frame) / ROWS_PER_PAGE))
    if st.session_state.get("dashboard_page", 1) > total_pages:
        st.session_state.dashboard_page = total_pages

    table_slot = st.container()
    with st.container(horizontal=True, horizontal_alignment="right"):
        page = st.pagination(
            total_pages,
            max_visible_pages=7,
            key="dashboard_page",
            persist_state="session",
        )
    start = (page - 1) * ROWS_PER_PAGE
    page_frame = sorted_frame.iloc[start : start + ROWS_PER_PAGE].reset_index(drop=True)
    with table_slot:
        _display_table(page_frame)
        st.caption("操作提示：鼠标点击或使用键盘聚焦并选择任意行，即可打开该条就诊记录的病情详情。")
