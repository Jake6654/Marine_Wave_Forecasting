"""SQLite의 최근 관측을 기존 특성 생성기와 학습 모델에 연결한다."""

import math
from datetime import datetime, timedelta, timezone

from marine_forecast.data.buoy_api import SOURCE_TIMEZONE
from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.data.live_normalization import (
    POLICY_VERSION, SOURCE_WARNINGS, normalize_live_observations, pressure_mapping_evidence,
)
from marine_forecast.features import FEATURE_COLUMNS, HORIZON_HOURS, build_features
from marine_forecast.model_artifact import ModelBundle, _validate_model


def predict_latest(
    store: BuoyObservationStore, bundle: ModelBundle, *,
    station_id: str = "22101", now: datetime | None = None,
    max_age_minutes: float = 90, allow_unverified_source: bool = False,
) -> dict[str, object]:
    """성공 결과 또는 명시적인 예측 불가 사유를 JSON 호환 객체로 반환한다."""
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime.")
    current = current.astimezone(timezone.utc)
    if (
        isinstance(max_age_minutes, bool) or not isinstance(max_age_minutes, (int, float))
        or not math.isfinite(max_age_minutes) or not 0 < max_age_minutes <= 180
    ):
        raise ValueError("max_age_minutes must be in (0, 180].")
    if type(allow_unverified_source) is not bool:
        raise TypeError("allow_unverified_source must be bool.")
    if station_id != "22101" or bundle.metadata.get("station_id") != station_id:
        raise ValueError("The model supports station 22101 only.")
    if (bundle.metadata.get("feature_columns") != list(FEATURE_COLUMNS)
            or bundle.metadata.get("horizon_hours") != HORIZON_HOURS):
        raise ValueError("Incompatible inference model contract.")
    _validate_model(bundle.model)
    result = {
        "status": "unavailable", "reason": None, "station_id": station_id,
        "generated_at_utc": current.isoformat(), "model_id": bundle.metadata["model_id"],
        "horizon_hours": HORIZON_HOURS, "policy_version": POLICY_VERSION,
        "quality_status": "unverified", "operational_use_allowed": False,
        "pressure_mapping_evidence": pressure_mapping_evidence(),
        "warnings": [*SOURCE_WARNINGS,
                     "Experimental point forecast, not an official forecast or navigation safety assessment."],
    }
    if not allow_unverified_source:
        # 과거 기압의 수치 일치가 확인돼도 미확인 QC를 우회하지 않는다.
        result["reason"] = "source_policy_unverified"
        return result

    # 현재 요청 시각 이후의 관측은 읽지 않아 미래 정보가 입력에 섞이지 않게 한다.
    raw = store.load(station_id=station_id, start=current - timedelta(hours=24), end=current)
    if not raw:
        result["reason"] = "no_recent_observations"
        return result
    frame, report = normalize_live_observations(raw)
    result["normalization_report"] = report
    result["latest_available_observation_at_utc"] = frame["observed_at"].max().isoformat()

    # 시간 자료로 학습했으므로 30분 자료를 평균내거나 정시로 반올림하지 않는다.
    hourly = frame.loc[frame["observed_at"].dt.minute.eq(0)].copy()
    if hourly.empty:
        result["reason"] = "no_hourly_observations"
        return result
    observed_at = hourly["observed_at"].max()
    target_at = observed_at + timedelta(hours=HORIZON_HOURS)
    age_minutes = (current - observed_at.to_pydatetime()).total_seconds() / 60
    result.update({
        "observed_at_utc": observed_at.isoformat(),
        "observed_at_kst": observed_at.tz_convert(SOURCE_TIMEZONE).isoformat(),
        "target_at_utc": target_at.isoformat(),
        "target_at_kst": target_at.tz_convert(SOURCE_TIMEZONE).isoformat(),
        "observation_age_minutes": age_minutes,
    })
    if age_minutes > max_age_minutes:
        result["reason"] = "stale_observation"
        return result

    # 2025년 덕적도 대조에서는 추가 기압 보정이 필요 없다는 사용자 근거를 기록한다.
    # 다른 기간까지 검증된 것은 아니므로 연결은 계속 명시적 실험 경로에 한정한다.
    observations = hourly.rename(columns={"api_pressure_hpa": "station_pressure_hpa"})
    features = build_features(observations)
    latest = features.loc[features["observed_at"].eq(observed_at), list(FEATURE_COLUMNS)]
    missing = latest.columns[latest.isna().any()].tolist()
    if missing:
        # 최신 행이 불완전해도 과거의 정상 행으로 몰래 후퇴하지 않는다.
        result.update(reason="incomplete_latest_features", missing_features=missing)
        return result
    inputs = latest.astype("float64")
    prediction = float(bundle.model.predict(inputs)[0])
    if not math.isfinite(prediction) or prediction < 0:
        result["reason"] = "invalid_model_prediction"
        return result
    used_times = {observed_at - timedelta(hours=hours) for hours in (0, 1, 3, 6)}
    result.update({
        "status": "experimental", "reason": None,
        "predicted_significant_wave_height_m": prediction,
        "persistence_significant_wave_height_m": float(inputs.iloc[0, 0]),
        "features": inputs.iloc[0].to_dict(),
        "source_qc": [
            {"observed_at_utc": row.observed_at.isoformat(),
             "aqc_raw": row.aqc_raw, "mqc_raw": row.mqc_raw}
            for row in hourly.itertuples() if row.observed_at in used_times
        ],
    })
    return result
