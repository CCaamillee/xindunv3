from __future__ import annotations

import streamlit as st


_PENDING_ENCOUNTER_KEY = "pending_encounter_key"


def normalize_encounter_key(encounter_key: object) -> str:
    """Return the stable ``regno::admno`` key used by patient-facing pages."""
    return str(encounter_key or "").strip()


def navigate_to_patient_page(page: str, encounter_key: object) -> None:
    """Queue a page change together with the exact encounter to open."""
    normalized = normalize_encounter_key(encounter_key)
    if not normalized:
        return
    st.session_state.selected_encounter_key = normalized
    st.session_state[_PENDING_ENCOUNTER_KEY] = normalized
    st.session_state.pending_page = page


def consume_pending_encounter_key() -> str | None:
    """Read a queued encounter once, after routing has reached the target page."""
    normalized = normalize_encounter_key(
        st.session_state.pop(_PENDING_ENCOUNTER_KEY, None)
    )
    return normalized or None


# Backward-compatible names for modules outside the primary five-page interface.
normalize_patient_id = normalize_encounter_key
consume_pending_patient_id = consume_pending_encounter_key
