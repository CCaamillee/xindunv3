from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from config import RISK_COLORS


PLOT_LAYOUT = dict(
    font=dict(family='"Segoe UI", "Microsoft YaHei UI", sans-serif', color="#415B72", size=12),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=28, b=20),
    hoverlabel=dict(bgcolor="#102A43", font_color="white"),
)


def cohort_distribution_chart(metrics: dict) -> go.Figure:
    counts = [metrics["break_count"], metrics["nonbreak_count"]]
    fig = go.Figure(
        go.Pie(
            labels=["心脏破裂组", "非破裂组"],
            values=counts,
            hole=0.68,
            marker_colors=[RISK_COLORS["HIGH"], "#4E91D8"],
            textinfo="percent",
            textposition="inside",
            insidetextfont=dict(color="white", size=11),
            hovertemplate="%{label}: %{value:,} 条<extra></extra>",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=280,
        showlegend=True,
        legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
        annotations=[
            dict(
                text=f"<b>{metrics['total']:,}</b><br><span style='font-size:11px'>就诊样本</span>",
                x=0.5,
                y=0.5,
                showarrow=False,
                font_size=17,
            )
        ],
    )
    return fig


def cohort_feature_chart(metrics: dict) -> go.Figure:
    groups = metrics["groups"]
    labels = ["年龄中位数（岁）", "LVEF中位数（%）"]
    break_values = [groups[1]["age_median"] or 0, groups[1]["lvef_median"] or 0]
    nonbreak_values = [groups[0]["age_median"] or 0, groups[0]["lvef_median"] or 0]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="心脏破裂组", x=labels, y=break_values, marker_color=RISK_COLORS["HIGH"]))
    fig.add_trace(go.Bar(name="非破裂组", x=labels, y=nonbreak_values, marker_color="#4E91D8"))
    fig.update_layout(
        **PLOT_LAYOUT,
        height=280,
        barmode="group",
        yaxis=dict(gridcolor="#E9EFF5"),
        legend=dict(orientation="h", y=1.12, x=0),
    )
    return fig


def feature_coverage_chart(rows: list[dict]) -> go.Figure:
    ordered = list(reversed(rows))
    fig = go.Figure(
        go.Bar(
            x=[row["total_count"] for row in ordered],
            y=[row["feature"] for row in ordered],
            orientation="h",
            marker_color="#16A7B7",
            hovertemplate="%{y}: %{x:,} 条记录<extra></extra>",
        )
    )
    fig.update_layout(**PLOT_LAYOUT, height=280, xaxis=dict(gridcolor="#E9EFF5"), yaxis=dict(title=None))
    return fig


def _distribution_bar_chart(
    rows: list[dict],
    colors: dict[str, str],
    *,
    height: int = 285,
) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=[row["label"] for row in rows],
            y=[row["count"] for row in rows],
            marker_color=[colors.get(row["key"], "#176BCE") for row in rows],
            text=[str(row["count"]) for row in rows],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}: %{y} 人<extra></extra>",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=height,
        showlegend=False,
        xaxis=dict(title=None, tickangle=0),
        yaxis=dict(title=None, rangemode="tozero", gridcolor="#E9EFF5", fixedrange=True),
    )
    return fig


def prediction_time_window_chart(rows: list[dict]) -> go.Figure:
    return _distribution_bar_chart(
        rows,
        {
            "day_0": "#D64545",
            "day_1": "#E26D4A",
            "day_2": "#D9912B",
            "day_1_2": "#D9912B",
            "day_3_14": "#4E91D8",
        },
    )


def prediction_risk_distribution_chart(rows: list[dict]) -> go.Figure:
    return _distribution_bar_chart(rows, RISK_COLORS)


def prediction_review_chart(rows: list[dict]) -> go.Figure:
    counts = [row["count"] for row in rows]
    fig = go.Figure(
        go.Pie(
            labels=[row["label"] for row in rows],
            values=counts,
            hole=0.64,
            marker_colors=["#D9912B", "#4E91D8"],
            textinfo="percent",
            textposition="inside",
            insidetextfont=dict(color="white", size=11),
            hovertemplate="%{label}: %{value} 人（%{percent}）<extra></extra>",
        )
    )
    total = sum(counts)
    fig.update_layout(
        **PLOT_LAYOUT,
        height=285,
        showlegend=True,
        legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
        annotations=[
            dict(
                text=f"<b>{total}</b><br><span style='font-size:11px'>已评估</span>",
                x=0.5,
                y=0.5,
                showarrow=False,
                font_size=17,
            )
        ],
    )
    return fig


def signal_curve(snapshots: list[dict], current_index: int | None = None, height: int = 320) -> go.Figure:
    visible = snapshots if current_index is None else snapshots[: current_index + 1]
    fig = go.Figure(
        go.Scatter(
            x=[row["time"] for row in visible],
            y=[row["review_signal_count"] for row in visible],
            text=[row["event"] for row in visible],
            mode="lines+markers",
            line=dict(color="#176BCE", width=3),
            marker=dict(size=8, color="#176BCE", line=dict(color="#FFF", width=2)),
            fill="tozeroy",
            fillcolor="rgba(23,107,206,.08)",
            hovertemplate="%{x}<br>累计复核信号 %{y}<br>%{text}<extra></extra>",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=height,
        yaxis=dict(dtick=1, rangemode="tozero", gridcolor="#E9EFF5"),
        showlegend=False,
    )
    return fig


def vital_snapshot_chart(rows: list[dict]) -> go.Figure:
    if not rows:
        fig = go.Figure()
        fig.add_annotation(text="当前患者无结构化生命体征", x=0.5, y=0.5, showarrow=False)
        fig.update_layout(**PLOT_LAYOUT, height=280, xaxis_visible=False, yaxis_visible=False)
        return fig
    df = pd.DataFrame(rows)
    plot_df = df.assign(numeric_value=pd.to_numeric(df["value"], errors="coerce")).dropna(
        subset=["numeric_value"]
    ).head(10)
    fig = go.Figure(
        go.Bar(
            x=plot_df["item"],
            y=plot_df["numeric_value"],
            marker_color="#176BCE",
            hovertemplate="%{x}: %{y}<extra></extra>",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=280,
        xaxis=dict(tickangle=-25),
        yaxis=dict(gridcolor="#E9EFF5"),
    )
    return fig
