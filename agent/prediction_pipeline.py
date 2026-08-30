from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from agent.risk_model import CardiacRuptureRiskModel


EncounterRecord = str | dict[str, Any]
ProgressCallback = Callable[[dict[str, Any]], None]
ContextProvider = Callable[[str], dict[str, Any]]
RiskPredictor = Callable[[str, dict[str, Any]], dict[str, Any]]


def _encounter_key(record: EncounterRecord) -> str:
    if isinstance(record, str):
        return record.strip()
    return str(record.get("encounter_key") or "").strip()


def _workbook_encounters() -> list[dict[str, Any]]:
    from services.workbook_data import get_encounters

    return get_encounters()


def _model_output(result: dict[str, Any]) -> dict[str, Any]:
    prediction = result.get("prediction") or {}
    fields = prediction.get("fields") or {}
    return {
        "rupture_label": fields.get("rupture_label"),
        "rupture_time_window": fields.get("rupture_time_window"),
        "evidence_confidence": fields.get("evidence_confidence"),
        "explanation": fields.get("explanation"),
        "thinking": prediction.get("thinking"),
        "answer": prediction.get("answer"),
    }


def run_all_patient_rupture_pipeline(
    encounter_records: Iterable[EncounterRecord] | None = None,
    *,
    limit: int | None = None,
    context_provider: ContextProvider | None = None,
    risk_predictor: RiskPredictor | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the uploaded cardiac-rupture model client over workbook encounters.

    The pipeline keeps each ``regno + admno`` encounter separate. It does not
    modify the Excel workbook and returns JSON-ready results to the caller.
    """
    if context_provider is None:
        from services.clinical_context import get_patient_clinical_context

        context_provider = get_patient_clinical_context
    if risk_predictor is None:
        model = CardiacRuptureRiskModel()

        def risk_predictor(encounter_key: str, context: dict[str, Any]) -> dict[str, Any]:
            return model.predict_with_context(encounter_key, context)

    raw_records = (
        list(encounter_records)
        if encounter_records is not None
        else _workbook_encounters()
    )
    encounter_keys: list[str] = []
    seen: set[str] = set()
    for record in raw_records:
        key = _encounter_key(record)
        if key and key not in seen:
            encounter_keys.append(key)
            seen.add(key)
    if limit is not None:
        encounter_keys = encounter_keys[: max(0, int(limit))]

    results: list[dict[str, Any]] = []
    total = len(encounter_keys)
    for index, encounter_key in enumerate(encounter_keys, start=1):
        try:
            context = context_provider(encounter_key)
            if context.get("error"):
                raise RuntimeError(str(context["error"]))
            prediction_result = risk_predictor(encounter_key, context)
            if prediction_result.get("error"):
                raise RuntimeError(str(prediction_result["error"]))
            item = {
                "encounter_key": encounter_key,
                "status": "completed",
                "model_output": _model_output(prediction_result),
            }
        except Exception as exc:
            item = {
                "encounter_key": encounter_key,
                "status": "failed",
                "error": str(exc),
                "model_output": None,
            }
        results.append(item)
        if progress_callback is not None:
            progress_callback(
                {
                    "index": index,
                    "total": total,
                    "encounter_key": encounter_key,
                    "status": item["status"],
                }
            )
    completed = sum(item["status"] == "completed" for item in results)
    return {
        "total": total,
        "completed": completed,
        "failed": total - completed,
        "results": results,
    }
