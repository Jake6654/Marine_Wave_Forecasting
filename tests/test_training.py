import hashlib
import importlib.util
import math
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN
from marine_forecast.training import train_ridge


REQUIRED_COLUMNS = (*FEATURE_COLUMNS, TARGET_COLUMN)


@pytest.fixture
def train_samples() -> pd.DataFrame:
    """열마다 서로 다른 크기를 갖는 작은 학습 표본을 만든다."""
    samples = pd.DataFrame({
        column: pd.Series(
            [(row + 1) * (position + 1) / 10 for row in range(8)],
            dtype="Float64",
        )
        for position, column in enumerate(FEATURE_COLUMNS)
    })
    samples[TARGET_COLUMN] = pd.Series(
        [0.5 + row / 10 for row in range(8)], dtype="Float64"
    )
    samples.index = range(10, 18)
    samples.attrs = {"source_timezone_status": "caller_supplied_unverified"}
    return samples


@pytest.fixture
def forbid_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """입력 오류가 있으면 학습을 시작하기 전에 실패해야 한다."""
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Invalid input reached Pipeline.fit().")

    monkeypatch.setattr(Pipeline, "fit", fail_if_called)


def test_returns_fitted_pipeline(train_samples: pd.DataFrame) -> None:
    model = train_ridge(train_samples)

    assert isinstance(model, Pipeline)
    assert isinstance(model.named_steps["scaler"], StandardScaler)
    assert isinstance(model.named_steps["ridge"], Ridge)
    assert model.named_steps["ridge"].coef_.shape == (7,)
    assert math.isfinite(model.named_steps["ridge"].intercept_)
    assert np.isfinite(model.predict(
        train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    )).all()


def test_scaler_uses_only_supplied_training_statistics(train_samples: pd.DataFrame) -> None:
    X_train = train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    scaler = train_ridge(train_samples).named_steps["scaler"]

    np.testing.assert_allclose(scaler.mean_, X_train.mean().to_numpy())
    np.testing.assert_allclose(scaler.var_, X_train.var(ddof=0).to_numpy())
    assert scaler.n_samples_seen_ == len(train_samples)


def test_prediction_does_not_refit_or_change_parameters(
    train_samples: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = train_ridge(train_samples)
    scaler = model.named_steps["scaler"]
    ridge = model.named_steps["ridge"]
    mean_before = scaler.mean_.copy()
    variance_before = scaler.var_.copy()
    coefficients_before = ridge.coef_.copy()
    intercept_before = ridge.intercept_

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Prediction must not call fit().")

    # 예측에서 어느 단계라도 다시 학습하면 즉시 실패하게 한다.
    for step in (model, scaler, ridge):
        monkeypatch.setattr(step, "fit", fail_if_called)
    validation = train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64") + 10000
    predicted = model.predict(validation)

    assert len(predicted) == len(validation)
    np.testing.assert_array_equal(scaler.mean_, mean_before)
    np.testing.assert_array_equal(scaler.var_, variance_before)
    np.testing.assert_array_equal(ridge.coef_, coefficients_before)
    assert ridge.intercept_ == intercept_before


def test_input_is_not_modified(train_samples: pd.DataFrame) -> None:
    original = train_samples.copy(deep=True)
    train_ridge(train_samples)
    assert_frame_equal(train_samples, original)
    assert train_samples.attrs == original.attrs


def test_selects_only_features_in_contract_order(train_samples: pd.DataFrame) -> None:
    train_samples["station_id"] = "22101"
    train_samples["observed_at"] = pd.date_range(
        "2025-01-01", periods=len(train_samples), freq="h", tz="UTC"
    )
    # 원본 열 순서가 바뀌어도 식별자와 미래 정답은 입력에서 제외한다.
    reordered = train_samples.loc[:, train_samples.columns[::-1]]
    model = train_ridge(reordered)
    assert model.feature_names_in_.tolist() == list(FEATURE_COLUMNS)
    assert model.n_features_in_ == 7


@pytest.mark.parametrize("alpha", [1, 2.5])
def test_applies_alpha(train_samples: pd.DataFrame, alpha: float) -> None:
    assert train_ridge(train_samples, alpha=alpha).named_steps["ridge"].alpha == alpha


@pytest.mark.parametrize("invalid", [None, [], pd.Series([1.0])])
def test_rejects_non_dataframe(invalid: object, forbid_fit: None) -> None:
    with pytest.raises(TypeError, match="pandas DataFrame"):
        train_ridge(invalid)


def test_rejects_empty_samples(train_samples: pd.DataFrame, forbid_fit: None) -> None:
    with pytest.raises(ValueError, match="No training samples"):
        train_ridge(train_samples.iloc[:0])


@pytest.mark.parametrize("alpha", [True, False, "1.0", None])
def test_rejects_invalid_alpha_type(
    train_samples: pd.DataFrame, alpha: object, forbid_fit: None
) -> None:
    with pytest.raises(TypeError, match="alpha must be a real number"):
        train_ridge(train_samples, alpha=alpha)


@pytest.mark.parametrize("alpha", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_rejects_invalid_alpha_value(
    train_samples: pd.DataFrame, alpha: float, forbid_fit: None
) -> None:
    with pytest.raises(ValueError, match="alpha must be finite and greater than zero"):
        train_ridge(train_samples, alpha=alpha)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_rejects_missing_columns(
    train_samples: pd.DataFrame, column: str, forbid_fit: None
) -> None:
    with pytest.raises(ValueError, match="Missing training columns"):
        train_ridge(train_samples.drop(columns=column))


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("kind", ["string", "boolean", "complex"])
def test_rejects_invalid_numeric_dtype_before_fit(
    train_samples: pd.DataFrame, column: str, kind: str, forbid_fit: None
) -> None:
    # 첫 열뿐 아니라 마지막 정답 열까지 모두 검사되는지 확인한다.
    dtype = {"string": "string", "boolean": "bool", "complex": "complex128"}[kind]
    train_samples[column] = train_samples[column].astype(dtype)
    with pytest.raises(TypeError, match=f"{column} must have a real numeric dtype"):
        train_ridge(train_samples)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("value", [pd.NA, float("nan")])
def test_rejects_missing_values_before_fit(
    train_samples: pd.DataFrame, column: str, value: object, forbid_fit: None
) -> None:
    train_samples.loc[10, column] = value
    with pytest.raises(ValueError, match=f"{column} must not contain missing values"):
        train_ridge(train_samples)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_rejects_infinity_before_fit(
    train_samples: pd.DataFrame, column: str, value: float, forbid_fit: None
) -> None:
    train_samples.loc[10, column] = value
    with pytest.raises(ValueError, match=f"{column} must contain finite values"):
        train_ridge(train_samples)


@pytest.fixture
def experiment() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts/train_ridge.py"
    spec = importlib.util.spec_from_file_location("ridge_test_experiment", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_validation_pipeline_integration(
    experiment: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """실제 학습·검증 표본이 스크립트에서 올바르게 연결되는지 확인한다."""
    paths = (experiment.DATA_PATH, experiment.DATA_PATH.with_suffix(".metadata.json"))
    if not all(path.is_file() for path in paths):
        pytest.skip("연간 정제 CSV와 메타데이터가 로컬에 없어 검증하지 못함")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    captured = {"evaluations": []}
    original_split = experiment.split_by_time
    original_train = experiment.train_ridge
    original_evaluate = experiment.evaluate_regression

    def capture_split(*args, **kwargs):
        result = original_split(*args, **kwargs)
        captured["splits"] = result[0]
        return result

    def capture_train(samples, **kwargs):
        captured["training_samples"] = samples.copy(deep=True)
        model = original_train(samples, **kwargs)
        captured["mean_before"] = model.named_steps["scaler"].mean_.copy()
        captured["variance_before"] = model.named_steps["scaler"].var_.copy()
        captured["coefficients_before"] = model.named_steps["ridge"].coef_.copy()
        captured["intercept_before"] = model.named_steps["ridge"].intercept_
        # 실제 예측은 실행하면서 입력과 호출 횟수만 기록한다.
        monkeypatch.setattr(model, "predict", Mock(wraps=model.predict))
        captured["model"] = model
        return model

    def capture_evaluate(actual, predicted):
        scores = original_evaluate(actual, predicted)
        captured["evaluations"].append((actual.copy(), predicted.copy(), scores))
        return scores

    monkeypatch.setattr(experiment, "split_by_time", capture_split)
    monkeypatch.setattr(experiment, "train_ridge", capture_train)
    monkeypatch.setattr(experiment, "evaluate_regression", capture_evaluate)

    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        returncode = experiment.main()

    assert returncode == 0
    train = captured["splits"]["train"]
    validation = captured["splits"]["validation"]
    assert (len(train), len(validation)) == (3891, 1130)
    assert_frame_equal(captured["training_samples"], train)
    assert train["target_at"].lt(pd.Timestamp("2025-10-01T00:00:00Z")).all()
    assert validation.attrs["source_timezone_status"] == "caller_supplied_unverified"

    model = captured["model"]
    scaler = model.named_steps["scaler"]
    ridge = model.named_steps["ridge"]
    X_train = train.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    X_validation = validation.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    np.testing.assert_allclose(scaler.mean_, X_train.mean().to_numpy())
    np.testing.assert_allclose(scaler.var_, X_train.var(ddof=0).to_numpy())
    assert scaler.n_samples_seen_ == 3891
    np.testing.assert_array_equal(scaler.mean_, captured["mean_before"])
    np.testing.assert_array_equal(scaler.var_, captured["variance_before"])
    np.testing.assert_array_equal(ridge.coef_, captured["coefficients_before"])
    assert ridge.intercept_ == captured["intercept_before"]

    # 검증 입력으로만 한 번 예측하고, 두 평가 모두 같은 정답을 사용해야 한다.
    assert model.predict.call_count == 1
    assert_frame_equal(model.predict.call_args.args[0], X_validation)
    assert len(captured["evaluations"]) == 2
    for actual, predicted, scores in captured["evaluations"]:
        assert_series_equal(actual, validation[TARGET_COLUMN])
        assert predicted.index.equals(validation.index)
        errors = [float(prediction - truth) for prediction, truth in zip(predicted, actual)]
        assert scores == pytest.approx({
            "mae_m": math.fsum(abs(error) for error in errors) / len(errors),
            "rmse_m": math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
        })

    output = capsys.readouterr().out
    assert "Training samples: 3891" in output
    assert "Validation samples: 1130" in output
    assert "Persistence:" in output and "Ridge:" in output
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert before == after
