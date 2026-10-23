"""API 원문을 품질 확인 상태와 함께 수치 관측으로 변환한다."""

import math
from datetime import timezone

import pandas as pd

from marine_forecast.data.buoy_api import SOURCE_TIMEZONE, RawBuoyObservation
from marine_forecast.data.buoy_store import _encode_observation


POLICY_VERSION = "kma-buoy-experimental-v2"
SOURCE_WARNINGS = (
    "AQC/MQC position mapping is unverified; observations are not QC-approved.",
    "PA matches training CSV pressure for station 22101 hourly data in 2025 per user report; official names differ and other periods/stations are not verified.",
    "WS1 is used without sensor fallback; equivalence across collection products is not guaranteed.",
)
FIELD_MAPPING = {
    "WH_SIG": "significant_wave_height_m",
    "WS1": "wind_speed_mps",
    # 2025년 수치 일치 근거와 공식 명칭의 차이를 분리하기 위해 출처 이름을 유지한다.
    "PA": "api_pressure_hpa",
}


def pressure_mapping_evidence() -> dict[str, object]:
    """사용자 보고의 범위와 확인 주체를 새 객체로 반환한다."""
    return {
        "status": "numerically_matched_user_reported",
        "station_id": "22101", "year_kst": 2025, "frequency": "hourly",
        "comparison_timezone": "Asia/Seoul", "csv_rows": 8043,
        "api_hourly_rows": 8043, "matching_timestamps": 8043,
        "pressure_mismatches": 0, "max_absolute_difference_hpa": 0.0,
        "csv_filename": "OBS_BUOY_TIM_20261021192756.csv",
        "api_field": "PA", "csv_field": "현지기압(hPa)",
        "reported_on": "2026-10-24", "evidence_provider": "user",
        "independently_reproduced_by_project": False,
        "official_naming_difference_resolved": False,
    }


def normalize_live_observations(
    observations: list[RawBuoyObservation],
) -> tuple[pd.DataFrame, dict[str, object]]:
    """원문을 변경하지 않고 결측 사유·QC 원문을 보존한 새 표를 만든다."""
    if not isinstance(observations, list):
        raise TypeError("observations must be a list.")
    rows = []
    issues = []
    seen = set()
    for observation in observations:
        # 직접 만든 객체도 파서·저장소와 같은 식별자 일관성을 만족해야 한다.
        _encode_observation(observation)
        key = (observation.station_id, observation.observed_at)
        if key in seen:
            raise ValueError("Duplicate live observation keys.")
        seen.add(key)
        row = {
            "station_id": observation.station_id,
            "observed_at": observation.observed_at.astimezone(timezone.utc),
            "aqc_raw": observation.get_raw("AQC"),
            "mqc_raw": observation.get_raw("MQC"),
        }
        for source, destination in FIELD_MAPPING.items():
            raw = observation.get_raw(source).strip()
            reason = None
            value = None
            try:
                value = float(raw)
            except ValueError:
                reason = "empty" if not raw else "invalid_number"
            if reason is None:
                if not math.isfinite(value):
                    reason = "non_finite"
                elif value <= -50:
                    reason = "source_missing_or_error"
                elif value < 0 or (source == "PA" and value == 0):
                    reason = "invalid_physical_range"
            # 잘못된 값은 0으로 대체하지 않고 결측과 구체적인 사유로 남긴다.
            row[destination] = pd.NA if reason else value
            if reason:
                issues.append({
                    "station_id": observation.station_id,
                    "observed_at": row["observed_at"].isoformat(),
                    "source_field": source, "reason": reason,
                })
        rows.append(row)

    columns = ["station_id", "observed_at", *FIELD_MAPPING.values(), "aqc_raw", "mqc_raw"]
    frame = pd.DataFrame(rows, columns=columns)
    frame["station_id"] = frame["station_id"].astype("string")
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    for column in FIELD_MAPPING.values():
        frame[column] = frame[column].astype("Float64")
    for column in ("aqc_raw", "mqc_raw"):
        frame[column] = frame[column].astype("string")
    frame = frame.sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    # UTC 연도가 아닌 원본 KST 연도로 사용자 대조 범위를 판단한다.
    in_comparison_scope = (
        frame["station_id"].eq("22101")
        & frame["observed_at"].dt.tz_convert(SOURCE_TIMEZONE).dt.year.eq(2025)
        & frame["observed_at"].dt.minute.eq(0)
    )
    report = {
        "policy_version": POLICY_VERSION,
        "qc_status": "unverified",
        "pressure_mapping_status": (
            "not_assessed" if frame.empty else
            "within_user_reported_comparison_scope" if in_comparison_scope.all() else
            "outside_user_reported_comparison_scope"
        ),
        "pressure_mapping_evidence": pressure_mapping_evidence(),
        "rows": len(frame), "issues": issues,
        "warnings": list(SOURCE_WARNINGS),
    }
    frame.attrs = {"source_timezone": "Asia/Seoul", "source_timezone_status": "confirmed"}
    return frame, report
