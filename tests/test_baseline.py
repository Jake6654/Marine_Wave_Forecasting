import hashlib
import math
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from marine_forecast.baseline import CURRENT_WAVE_COLUMN, predict_persistence
from marine_forecast.dataset import (
    attach_targets,
    load_processed_data,
    select_complete_samples,
    split_by_time,
)
from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN, build_features
from marine_forecast.metrics import evaluate_regression


@pytest.fixture
def features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            CURRENT_WAVE_COLUMN: pd.array([0.0, 0.5, 1.3], dtype="Float64"),
            "wind_speed_mps": pd.array([pd.NA, 4.7, 7.2], dtype="Float64"),
        },
        index=[10, 20, 30],
    )


def test_predict_copies_current_wave_and_preserves_input(features: pd.DataFrame) -> None:
    original = features.copy(deep=True)

    predicted = predict_persistence(features)

    expected = pd.Series(
        [0.0, 0.5, 1.3],
        index=[10, 20, 30],
        dtype="float64",
        name="predicted_significant_wave_height_6h_m",
    )
    assert_series_equal(predicted, expected)
    assert_frame_equal(features, original)

    # 예측 결과를 바꾸더라도 원본 관측값이 변하지 않는지 확인한다.
    predicted.loc[20] = 99.0
    assert_frame_equal(features, original)


def test_predict_only_needs_current_wave(features: pd.DataFrame) -> None:
    predicted = predict_persistence(features[[CURRENT_WAVE_COLUMN]])
    assert predicted.tolist() == [0.0, 0.5, 1.3]


@pytest.mark.parametrize("dtype", ["int64", "Int64", "float64", "Float64"])
def test_predict_accepts_real_numeric_dtypes(dtype: str) -> None:
    frame = pd.DataFrame({CURRENT_WAVE_COLUMN: pd.Series([0, 1, 2], dtype=dtype)})
    assert predict_persistence(frame).tolist() == [0.0, 1.0, 2.0]


@pytest.mark.parametrize("value", [None, [0.5], pd.Series([0.5])])
def test_predict_rejects_non_dataframe(value: object) -> None:
    with pytest.raises(TypeError, match="pandas DataFrame"):
        predict_persistence(value)


def test_predict_rejects_missing_column() -> None:
    with pytest.raises(ValueError, match="Missing feature column"):
        predict_persistence(pd.DataFrame({"unexpected": [0.5]}))


def test_predict_rejects_empty_input(features: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="No feature rows"):
        predict_persistence(features.iloc[:0])


def test_predict_rejects_duplicate_index(features: pd.DataFrame) -> None:
    features.index = [10, 10, 20]
    with pytest.raises(ValueError, match="index must be unique"):
        predict_persistence(features)


@pytest.mark.parametrize("values", [["0.5"], [True], [0.5 + 0j]])
def test_predict_rejects_invalid_dtypes(values: list) -> None:
    with pytest.raises(TypeError, match="real numeric dtype"):
        predict_persistence(pd.DataFrame({CURRENT_WAVE_COLUMN: values}))


@pytest.mark.parametrize("value", [pd.NA, float("nan")])
def test_predict_rejects_missing_current_wave(features: pd.DataFrame, value: object) -> None:
    features.loc[20, CURRENT_WAVE_COLUMN] = value
    with pytest.raises(ValueError, match="must not be missing"):
        predict_persistence(features)


@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_predict_rejects_infinite_current_wave(features: pd.DataFrame, value: float) -> None:
    features.loc[20, CURRENT_WAVE_COLUMN] = value
    with pytest.raises(ValueError, match="must be finite"):
        predict_persistence(features)


def test_predict_rejects_negative_wave(features: pd.DataFrame) -> None:
    features.loc[20, CURRENT_WAVE_COLUMN] = -0.1
    with pytest.raises(ValueError, match="must be non-negative"):
        predict_persistence(features)


def test_validation_pipeline_integration() -> None:
    """실제 연간 데이터의 검증 구간만 사용해 기준 예측과 평가를 연결한다."""
    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data/processed/buoy_22101_2025_utc_assumed.csv"
    metadata_path = csv_path.with_suffix(".metadata.json")
    if not csv_path.is_file() or not metadata_path.is_file():
        pytest.skip("연간 정제 CSV와 메타데이터가 로컬에 없어 검증하지 못함")

    paths = (csv_path, metadata_path)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        observations, metadata = load_processed_data(csv_path)
    features = build_features(observations)
    dataset = attach_targets(features, observations)
    complete, _ = select_complete_samples(dataset)
    splits, _ = split_by_time(
        complete,
        validation_start="2025-10-01T00:00:00Z",
        test_start="2025-12-01T00:00:00Z",
        test_end="2026-01-01T00:00:00Z",
    )

    # 최종 테스트 구간의 오차는 모델 선택이 끝날 때까지 계산하지 않는다.
    validation = splits["validation"]
    predicted = predict_persistence(validation.loc[:, list(FEATURE_COLUMNS)])
    actual = validation[TARGET_COLUMN]
    scores = evaluate_regression(actual, predicted)

    assert len(validation) == 1130
    assert_series_equal(
        predicted,
        validation[CURRENT_WAVE_COLUMN].astype("float64"),
        check_names=False,
    )
    assert validation.attrs["source_timezone_status"] == metadata["source_timezone_status"]

    # pandas의 지표 계산과 별도로 Python 수치 합산으로 기대값을 확인한다.
    errors = [float(prediction - truth) for prediction, truth in zip(predicted, actual)]
    assert scores == pytest.approx({
        "mae_m": math.fsum(abs(error) for error in errors) / len(errors),
        "rmse_m": math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
    })
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert before == after

@pytest.mark.parametrize(
    "predicted_value,expected_code,expected_label",
    [
        (0.18, "P1", "1m 미만"),
        (1.0, "P2", "1~2m 미만"),
        (2.0, "P3", "2~3m 미만"),
        (3.0, "P4", "3m 이상"),
    ],
)
def test_prediction_response_contains_wave_reference(
    resources,
    monkeypatch,
    predicted_value,
    expected_code,
    expected_label,
):
    service_module = importlib.import_module(
        "marine_forecast.api.service"
    )
    original_predict = service_module.predict_latest

    def controlled_prediction(*args, **kwargs):
        result = original_predict(*args, **kwargs)

        # HTTP 연결 검증을 위해 예측 수치만 결정적으로 변경한다.
        result["predicted_significant_wave_height_m"] = (
            predicted_value
        )
        return result

    monkeypatch.setattr(
        service_module,
        "predict_latest",
        controlled_prediction,
    )

    with client_for(resources[1]) as client:
        response = client.get(PRED)

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "experimental"
    assert body["wave_reference"] == {
        "code": expected_code,
        "label": expected_label,
        "policy_version": "wave-reference-v1",
    }

    # 구간이 생겨도 QC 상태나 운영 사용 허용 여부는 바뀌지 않는다.
    assert body["quality_status"] == "unverified"
    assert body["operational_use_allowed"] is False


@pytest.mark.parametrize(
    "scenario,expected_reason",
    [
        ("policy", "source_policy_unverified"),
        ("stale", "stale_observation"),
        ("missing", "incomplete_latest_features"),
    ],
)
def test_unavailable_prediction_has_null_wave_reference(
    resources,
    scenario,
    expected_reason,
):
    store, settings = resources
    now = NOW

    if scenario == "policy":
        settings = replace(
            settings,
            allow_unverified_source=False,
        )

    elif scenario == "stale":
        now = NOW + timedelta(hours=2)

    else:
        # 最新ではなく 최신 정시의 풍속을 결측 처리한다.
        store.save(
            [
                make_row(
                    NOW.replace(minute=0),
                    WS1="-99",
                )
            ],
            received_at=NOW + timedelta(seconds=1),
        )

    app = create_app(settings, clock=lambda: now)

    with TestClient(app) as client:
        response = client.get(PRED)

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "unavailable"
    assert body["reason"] == expected_reason
    assert body["wave_reference"] is None
    assert "predicted_significant_wave_height_m" not in body
    assert body["operational_use_allowed"] is False
