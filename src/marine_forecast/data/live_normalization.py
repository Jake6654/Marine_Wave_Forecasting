"""API 원문을 품질 확인 상태와 함께 수치 관측으로 변환한다."""

import math
from datetime import timezone

import pandas as pd

from marine_forecast.data.buoy_api import RawBuoyObservation
from marine_forecast.data.buoy_store import _encode_observation


POLICY_VERSION = "kma-buoy-experimental-v1"
SOURCE_WARNINGS = (
    "AQC/MQC position mapping is unverified; observations are not QC-approved.",
    "API PA is documented as sea-level pressure; equivalence to training station pressure is unverified.",
    "WS1 is used without sensor fallback; equivalence across collection products is not guaranteed.",
)
FIELD_MAPPING = {
    "WH_SIG": "significant_wave_height_m",
    "WS1": "wind_speed_mps",
    # 의미 확인 전에는 현지기압이라는 내부 이름으로 바꾸지 않는다.
    "PA": "api_pressure_hpa",
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
    report = {
        "policy_version": POLICY_VERSION,
        "qc_status": "unverified", "pressure_mapping_status": "unverified",
        "rows": len(frame), "issues": issues,
        "warnings": list(SOURCE_WARNINGS),
    }
    frame.attrs = {"source_timezone": "Asia/Seoul", "source_timezone_status": "confirmed"}
    return frame, report
