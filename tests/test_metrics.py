import math
import warnings

import pandas as pd
import pytest
from pandas.testing import assert_series_equal

from marine_forecast.metrics import evaluate_regression


def test_known_scores_and_input_preservation() -> None:
    actual = pd.Series([0.6, 1.0], index=[10, 20], dtype="Float64")
    predicted = pd.Series([0.5, 1.3], index=[10, 20], dtype="float64")
    original_actual = actual.copy(deep=True)
    original_predicted = predicted.copy(deep=True)

    scores = evaluate_regression(actual, predicted)

    assert scores == pytest.approx({"mae_m": 0.2, "rmse_m": math.sqrt(0.05)})
    assert all(type(value) is float for value in scores.values())
    assert_series_equal(actual, original_actual)
    assert_series_equal(predicted, original_predicted)


def test_perfect_prediction_has_zero_error() -> None:
    actual = pd.Series([0.0, 0.6, 1.3], dtype="Float64")
    assert evaluate_regression(actual, actual.copy()) == {"mae_m": 0.0, "rmse_m": 0.0}


def test_opposite_errors_do_not_cancel() -> None:
    actual = pd.Series([1, 1], dtype="int64")
    predicted = pd.Series([0, 2], dtype="Int64")
    assert evaluate_regression(actual, predicted) == {"mae_m": 1.0, "rmse_m": 1.0}


def test_negative_prediction_can_be_evaluated() -> None:
    assert evaluate_regression(pd.Series([0.0]), pd.Series([-0.5])) == {
        "mae_m": 0.5, "rmse_m": 0.5,
    }


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
@pytest.mark.parametrize("invalid", [None, [1.0], pd.DataFrame({"value": [1.0]})])
def test_rejects_non_series(side: str, invalid: object) -> None:
    values = {"y_true": pd.Series([1.0]), "y_pred": pd.Series([1.0])}
    values[side] = invalid
    with pytest.raises(TypeError, match=f"{side} must be a pandas Series"):
        evaluate_regression(**values)


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
def test_rejects_empty_series(side: str) -> None:
    values = {"y_true": pd.Series([1.0]), "y_pred": pd.Series([1.0])}
    values[side] = pd.Series([], dtype="float64")
    with pytest.raises(ValueError, match=f"{side} must not be empty"):
        evaluate_regression(**values)


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
def test_rejects_duplicate_indexes(side: str) -> None:
    values = {"y_true": pd.Series([1.0, 2.0]), "y_pred": pd.Series([1.0, 2.0])}
    values[side].index = [0, 0]
    with pytest.raises(ValueError, match=f"{side} index must be unique"):
        evaluate_regression(**values)


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
@pytest.mark.parametrize("invalid", [pd.Series(["1.0"]), pd.Series([True]), pd.Series([1 + 0j])])
def test_rejects_non_real_numeric_dtypes(side: str, invalid: pd.Series) -> None:
    values = {"y_true": pd.Series([1.0]), "y_pred": pd.Series([1.0])}
    values[side] = invalid
    with pytest.raises(TypeError, match=f"{side} must have a real numeric dtype"):
        evaluate_regression(**values)


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
@pytest.mark.parametrize("value", [pd.NA, float("nan")])
def test_rejects_missing_values(side: str, value: object) -> None:
    values = {"y_true": pd.Series([1.0]), "y_pred": pd.Series([1.0])}
    values[side] = pd.Series([value], dtype="Float64")
    with pytest.raises(ValueError, match=f"{side} must not contain missing values"):
        evaluate_regression(**values)


@pytest.mark.parametrize("side", ["y_true", "y_pred"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_rejects_infinite_values(side: str, value: float) -> None:
    values = {"y_true": pd.Series([1.0]), "y_pred": pd.Series([1.0])}
    values[side] = pd.Series([value])
    with pytest.raises(ValueError, match=f"{side} must contain finite values"):
        evaluate_regression(**values)


@pytest.mark.parametrize("index", [["B", "A"], ["A", "C"], ["A"]])
def test_rejects_mismatched_indexes(index: list[str]) -> None:
    actual = pd.Series([0.6, 1.0], index=["A", "B"])
    predicted = pd.Series([0.5] * len(index), index=index)
    with pytest.raises(ValueError, match="identical indexes in the same order"):
        evaluate_regression(actual, predicted)


def test_rejects_overflowed_metric_result() -> None:
    # 유한한 큰 입력도 제곱 중 넘칠 수 있으므로 결과 검사까지 확인한다.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(ValueError, match="non-finite result"):
            evaluate_regression(pd.Series([0.0]), pd.Series([1e308]))
