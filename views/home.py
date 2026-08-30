from __future__ import annotations

import streamlit as st

from config import APP_NAME, APP_POSITIONING, APP_SUBTITLE


HERO_ILLUSTRATION_PATH = "assets/home-hero-heart-cartoon.png"


def _go(page: str) -> None:
    st.session_state.pending_page = page


def _feature_card(
    container,
    *,
    title: str,
    description: str,
    icon: str,
    page: str,
) -> None:
    with container.container(border=True, height="stretch"):
        st.html(
            f"""
            <div class="home-feature-content">
              <div class="feature-icon" aria-hidden="true">
                <span class="material-symbols-rounded">{icon}</span>
              </div>
              <div class="feature-title">{title}</div>
              <div class="feature-copy">{description}</div>
            </div>
            """
        )
        st.button(
            f"进入{title}",
            icon=":material/arrow_forward:",
            type="primary",
            width="stretch",
            key=f"home_to_{page}",
            on_click=_go,
            args=(page,),
        )


def render() -> None:
    with st.container(key="home_page", gap="small"):
        hero_text, hero_logo = st.columns(
            [3.25, 1.3],
            gap="medium",
            vertical_alignment="center",
        )
        with hero_text:
            st.html(
                f"""
                <div class="home-hero">
                  <div class="hero-kicker">CLINICAL DECISION SUPPORT</div>
                  <div class="hero-title">{APP_NAME}｜{APP_SUBTITLE}</div>
                  <div class="hero-copy">{APP_POSITIONING}。系统仅整理上传工作簿中的真实就诊记录，帮助医护人员快速定位资料、核对风险信息并追踪病情过程。</div>
                  <div class="hero-meta">
                    <span>心脏破裂风险监测</span>
                    <span>急诊患者概览</span>
                    <span>辅助诊断核对</span>
                    <span>就诊级时间轴</span>
                  </div>
                </div>
                """
            )
        with hero_logo:
            with st.container(
                horizontal_alignment="center",
                key="home_hero_logo",
            ):
                st.image(HERO_ILLUSTRATION_PATH, width=300)

        cards = st.columns(3, gap="small")
        _feature_card(
            cards[0],
            title="急诊概览",
            description="查看患者与就诊总体情况、目标事件标签、手术记录覆盖和可点击患者清单。",
            icon="emergency",
            page="急诊概览",
        )
        _feature_card(
            cards[1],
            title="辅助诊断",
            description="组合搜索患者，分组查看诊断、检查检验、治疗记录和风险字段可用性。",
            icon="clinical_notes",
            page="辅助诊断",
        )
        _feature_card(
            cards[2],
            title="病情详情",
            description="严格按 regno 与 admno 区分就诊，沿真实时间字段追踪完整病情过程。",
            icon="timeline",
            page="病情详情",
        )

        st.info(
            "本系统用于临床辅助和科研分析，不能替代医生诊断、处置或治疗决策。",
            icon=":material/info:",
        )
