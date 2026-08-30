from __future__ import annotations

import html

import streamlit as st

from components.header import render_header, section_header
from config import APP_NAME, APP_SUBTITLE


ABOUT_CARD_IMAGES = {
    "emergency": "assets/about-emergency-overview.png",
    "diagnosis": "assets/about-assisted-diagnosis.png",
    "timeline": "assets/about-patient-timeline.png",
}


def _about_card(
    container: st.delta_generator.DeltaGenerator,
    *,
    slug: str,
    icon: str,
    title: str,
    text: str,
    points: tuple[str, ...],
) -> None:
    point_markup = "".join(
        "<div class='about-feature-point'>"
        "<span class='material-symbols-rounded' aria-hidden='true'>check_circle</span>"
        f"<span>{html.escape(point)}</span>"
        "</div>"
        for point in points
    )
    with container.container(
        border=False,
        height="stretch",
        key=f"about_card_{slug}",
    ):
        st.image(ABOUT_CARD_IMAGES[slug], width="stretch")
        st.html(
            f"""
            <article class="about-feature-content">
              <div class="about-feature-icon" aria-hidden="true">
                <span class="material-symbols-rounded">{icon}</span>
              </div>
              <div class="about-feature-title">{html.escape(title)}</div>
              <div class="about-feature-copy">{html.escape(text)}</div>
              <div class="about-feature-points">{point_markup}</div>
            </article>
            """
        )


def render() -> None:
    render_header(
        "关于",
        f"了解 {APP_NAME} 的功能定位、页面能力与临床使用原则。",
    )

    st.markdown(
        f"""
        <div class="about-callout">
          <div class="about-callout-title">{APP_SUBTITLE}</div>
          <p>
            {APP_NAME} 面向心脏破裂相关临床辅助与科研分析场景，对上传工作簿中的真实就诊资料进行结构化整理，
            将分散在基本信息、诊断、检查检验、治疗与病程记录中的内容集中呈现，帮助医护人员更高效地查找资料、
            核对关键记录并回顾一次就诊过程。
          </p>
          <p>
            系统以一次就诊作为基本浏览边界，优先使用 <code>regno</code> 识别患者、使用 <code>admno</code>
            区分同一患者的不同就诊，避免将多次就诊记录错误合并。页面仅展示工作簿已有字段及项目现有逻辑能够确认的结果，
            对缺失信息明确标注“暂无记录”或“无法判断”；系统负责组织和呈现证据，不替代医生的临床判断与诊疗决策。
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    section_header("主要功能页面")
    cards = st.columns(3, gap="medium")
    _about_card(
        cards[0],
        slug="emergency",
        icon="emergency",
        title="急诊概览",
        text=(
            "作为系统的集中浏览入口，急诊概览以真实患者和就诊记录为基础，汇总工作簿覆盖的患者数量、就诊范围及可验证的关键字段。"
            "医护人员可以先从整体情况识别需要重点核对的记录，再通过编号、诊断和时间等条件缩小范围，减少在大量病历资料中逐条查找的负担。"
        ),
        points=(
            "汇总患者、就诊、目标事件标签及手术记录等真实数据覆盖情况",
            "支持按患者编号、就诊编号及已有临床字段进行检索与组合筛选",
            "患者列表支持排序和分页，便于在较多就诊记录中持续浏览",
            "点击患者行可携带稳定标识，直接进入对应患者与对应就诊详情",
        ),
    )
    _about_card(
        cards[1],
        slug="diagnosis",
        icon="clinical_notes",
        title="辅助诊断",
        text=(
            "辅助诊断将患者选择、资料核对与 Agent 问答整合在同一工作区，并始终以当前选择的一次就诊作为回答和展示边界。"
            "左侧用于检索患者、确认就诊并查看结构化资料，右侧用于围绕主要病历、检查检验、临床时间轴及风险字段边界提出问题，方便医护人员交叉核对证据。"
        ),
        points=(
            "可按患者编号、就诊编号以及工作簿已有临床字段组合查找",
            "将基本信息、诊断、检查检验、治疗及病程资料分组呈现",
            "常用问题入口帮助快速核对主要病历、检查检验和临床时间轴",
            "Agent 回答限定于当前就诊资料，模型不可用或信息不足时明确提示",
        ),
    )
    _about_card(
        cards[2],
        slug="timeline",
        icon="timeline",
        title="病情详情",
        text=(
            "病情详情围绕患者的一次就诊组织完整资料，将基本信息、诊断、检查检验、治疗、手术与病程记录按类别归纳，"
            "并使用工作簿中的真实日期时间字段构建纵向时间轴。用户可以从门急诊或入院节点开始，连续回顾治疗过程、出院记录或数据窗口截止前的资料变化。"
        ),
        points=(
            "使用 regno 识别患者、使用 admno 明确区分同一患者的不同就诊",
            "时间轴节点依据工作簿真实日期时间排序，不补造缺失事件或时间",
            "关键节点同时显示事件类型、摘要与数据类别，详细内容可按需展开",
            "支持分组查看基本信息、诊断、检查检验、治疗病程及风险相关字段",
        ),
    )

    section_header("临床使用原则")
    with st.container(border=True, key="about_clinical_notice"):
        st.subheader(":material/health_and_safety: 临床提示")
        st.markdown(
            """
            - 系统仅用于临床辅助和科研分析，不能替代医生诊断、处置或治疗决策。
            - 风险结果必须结合完整病历、检查、检验及床旁情况综合判断。
            - 缺失字段按“暂无记录”或“无法判断”显示，不将缺失等同于正常或阴性。
            - `label` 是回顾性目标事件标签，`cutoff_time` 是 15 天数据窗口截止时间；二者不代表预测概率、风险等级或预测破裂时间。
            """
        )
