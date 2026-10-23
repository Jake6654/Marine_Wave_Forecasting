"""합성 원문과 실제 SQLite로 실시간 변환·추론 연결을 검증한다."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest
from sklearn.ensemble import RandomForestRegressor

from marine_forecast.data.buoy_api import SOURCE_COLUMNS, SOURCE_TIMEZONE, parse_buoy_response
from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.data.live_normalization import normalize_live_observations
from marine_forecast.features import FEATURE_COLUMNS
from marine_forecast.inference import predict_latest
from marine_forecast.model_artifact import load_model_artifact, save_model_artifact


NOW = datetime(2026, 10, 24, 0, 10, tzinfo=timezone.utc)


def raw_observation(at: datetime, **changes):
    fields = dict.fromkeys(SOURCE_COLUMNS, "0")
    fields.update(TM=at.astimezone(SOURCE_TIMEZONE).strftime("%Y%m%d%H%M"),
                  STN="22101", WH_SIG=".5", WS1="2.0", WS2="9.0", PA="1018.0",
                  AQC="00000000000000//000", MQC="---------------")
    fields.update(changes)
    return parse_buoy_response("\n".join([
        "#START7777", ",".join([*(fields[name] for name in SOURCE_COLUMNS), "="]),
        "#7777END",
    ]))[0]


@pytest.fixture
def raw_rows():
    base = NOW.replace(minute=0)
    return [raw_observation(base - timedelta(hours=hours), WH_SIG=str(.5 + hours / 10))
            for hours in range(8)]


@pytest.fixture
def bundle(tmp_path):
    inputs = pd.DataFrame({name: [float(i) for i in range(12)] for name in FEATURE_COLUMNS})
    model = RandomForestRegressor(n_estimators=5, random_state=42).fit(inputs, [0.5] * 12)
    path = tmp_path / "model"
    save_model_artifact(model, path, provenance={"station_id": "22101"})
    return load_model_artifact(path, trusted=True)


@pytest.fixture
def store(tmp_path, raw_rows):
    store = BuoyObservationStore(tmp_path / "buoy.sqlite3")
    store.initialize()
    store.save(raw_rows, received_at=NOW)
    return store


def predict(store, bundle, **kwargs):
    return predict_latest(store, bundle, now=NOW, allow_unverified_source=True, **kwargs)


def test_normalization_schema_and_source_preservation(raw_rows):
    original = list(raw_rows)
    frame, report = normalize_live_observations(raw_rows)
    assert raw_rows == original
    assert str(frame["station_id"].dtype) == "string"
    assert str(frame["observed_at"].dt.tz) == "UTC"
    assert str(frame["wind_speed_mps"].dtype) == "Float64"
    assert frame["wind_speed_mps"].eq(2).all()
    assert "station_pressure_hpa" not in frame
    assert frame["api_pressure_hpa"].eq(1018).all()
    assert frame["aqc_raw"].eq("00000000000000//000").all()
    assert report["qc_status"] == "unverified"
    assert report["issues"] == []


@pytest.mark.parametrize("value,reason", [
    ("", "empty"), ("broken", "invalid_number"), ("NaN", "non_finite"),
    ("inf", "non_finite"), ("-inf", "non_finite"), ("-50", "source_missing_or_error"),
    ("-99.9", "source_missing_or_error"), ("-1", "invalid_physical_range"),
])
@pytest.mark.parametrize("source", ["WH_SIG", "WS1", "PA"])
def test_invalid_numbers_become_missing_with_reason(value, reason, source):
    frame, report = normalize_live_observations([raw_observation(NOW, **{source: value})])
    assert frame.isna().sum().sum() == 1
    assert report["issues"][0]["reason"] == reason
    assert report["issues"][0]["source_field"] == source


def test_zero_is_not_missing_and_wind_sensor_fallback_is_not_used():
    frame, _ = normalize_live_observations([raw_observation(NOW, WH_SIG="0", WS1="0")])
    assert frame.iloc[0]["significant_wave_height_m"] == 0
    assert frame.iloc[0]["wind_speed_mps"] == 0
    frame, report = normalize_live_observations([raw_observation(NOW, WS1="-99", WS2="9")])
    assert pd.isna(frame.iloc[0]["wind_speed_mps"])
    assert len(report["issues"]) == 1


def test_zero_pressure_is_invalid():
    frame, _ = normalize_live_observations([raw_observation(NOW, PA="0")])
    assert pd.isna(frame.iloc[0]["api_pressure_hpa"])


def test_empty_normalization_has_stable_schema():
    frame, report = normalize_live_observations([])
    assert frame.empty and report["rows"] == 0
    assert str(frame["observed_at"].dt.tz) == "UTC"


def test_normalization_rejects_duplicate_and_mismatched_keys(raw_rows):
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_live_observations([raw_rows[0], raw_rows[0]])
    with pytest.raises(ValueError, match="match"):
        normalize_live_observations([replace(raw_rows[0], station_id="99999")])


def test_default_policy_does_not_claim_quality_is_confirmed(store, bundle):
    result = predict_latest(store, bundle, now=NOW)
    assert result["status"] == "unavailable"
    assert result["reason"] == "source_policy_unverified"
    assert "predicted_significant_wave_height_m" not in result


def test_db_to_features_to_reloaded_model_integration(store, bundle):
    result = predict(store, bundle)
    assert result["status"] == "experimental"
    assert result["predicted_significant_wave_height_m"] == pytest.approx(.5)
    assert result["features"]["wave_height_lag_1h_m"] == pytest.approx(.6)
    assert result["features"]["wave_height_lag_3h_m"] == pytest.approx(.8)
    assert result["features"]["wave_height_lag_6h_m"] == pytest.approx(1.1)
    assert result["features"]["wave_height_change_1h_m"] == pytest.approx(-.1)
    assert list(result["features"]) == list(FEATURE_COLUMNS)
    assert result["observation_age_minutes"] == 10
    assert result["observed_at_kst"] == "2026-10-24T09:00:00+09:00"
    assert result["target_at_kst"] == "2026-10-24T15:00:00+09:00"
    assert result["operational_use_allowed"] is False
    assert result["quality_status"] == "unverified"
    assert len(result["source_qc"]) == 4
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("hours", [1, 3, 6])
def test_missing_exact_lag_is_not_previous_row(tmp_path, bundle, raw_rows, hours):
    store = BuoyObservationStore(tmp_path / "missing.sqlite3")
    store.initialize()
    rows = [row for row in raw_rows if row.observed_at != NOW.replace(minute=0) - timedelta(hours=hours)]
    # 30분 전후 행이 있어도 빠진 정시 값을 대신하면 안 된다.
    rows.append(raw_observation(NOW.replace(minute=0) - timedelta(hours=hours, minutes=30)))
    store.save(rows, received_at=NOW)
    result = predict(store, bundle)
    assert result["reason"] == "incomplete_latest_features"
    assert f"wave_height_lag_{hours}h_m" in result["missing_features"]


def test_invalid_latest_does_not_fall_back_to_older_valid_row(store, bundle):
    store.save([raw_observation(NOW.replace(minute=0), WS1="-99")], received_at=NOW + timedelta(seconds=1))
    result = predict(store, bundle)
    assert result["reason"] == "incomplete_latest_features"
    assert result["missing_features"] == ["wind_speed_mps"]


def test_half_hour_and_future_values_do_not_change_hourly_forecast(store, bundle):
    before = predict(store, bundle)
    store.save([raw_observation(NOW.replace(minute=0) - timedelta(minutes=30), WH_SIG="99"),
                raw_observation(NOW.replace(minute=30), WH_SIG="99")], received_at=NOW)
    after = predict(store, bundle)
    assert before["features"] == after["features"]
    assert before["observed_at_utc"] == after["observed_at_utc"]


def test_stale_observation_is_rejected(store, bundle):
    result = predict_latest(store, bundle, now=NOW + timedelta(hours=2), allow_unverified_source=True)
    assert result["reason"] == "stale_observation"


def test_empty_store_and_half_hour_only_have_explicit_reasons(tmp_path, bundle):
    store = BuoyObservationStore(tmp_path / "empty.sqlite3")
    store.initialize()
    assert predict(store, bundle)["reason"] == "no_recent_observations"
    store.save([raw_observation(NOW.replace(minute=0) - timedelta(minutes=30))], received_at=NOW)
    assert predict(store, bundle)["reason"] == "no_hourly_observations"


@pytest.mark.parametrize("age", [0, -1, float("inf"), float("nan"), True, 181])
def test_invalid_freshness_configuration(store, bundle, age):
    with pytest.raises(ValueError, match="max_age"):
        predict(store, bundle, max_age_minutes=age)


def test_naive_now_other_station_and_incompatible_bundle_are_rejected(store, bundle):
    with pytest.raises(ValueError, match="timezone"):
        predict_latest(store, bundle, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="22101"):
        predict(store, bundle, station_id="99999")
    bad = replace(bundle, metadata={**bundle.metadata, "horizon_hours": 3})
    with pytest.raises(ValueError, match="contract"):
        predict(store, bad)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_model_prediction_is_not_clipped(store, bundle, monkeypatch, value):
    monkeypatch.setattr(bundle.model, "predict", lambda _: [value])
    assert predict(store, bundle)["reason"] == "invalid_model_prediction"
