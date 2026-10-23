import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from sklearn.ensemble import RandomForestRegressor

from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN
from marine_forecast.training import train_random_forest


REQUIRED_COLUMNS = (*FEATURE_COLUMNS, TARGET_COLUMN)
PARAMETER_NAMES = ("n_estimators", "max_depth", "min_samples_leaf")


@pytest.fixture
def train_samples() -> pd.DataFrame:
    """조건 분할이 가능한 작은 표본과 독립적인 행 라벨을 만든다."""
    samples = pd.DataFrame({
        column: pd.Series(
            [((row + 1) * (position + 1)) % 13 / 10 for row in range(24)],
            dtype="Float64",
        )
        for position, column in enumerate(FEATURE_COLUMNS)
    })
    samples[TARGET_COLUMN] = pd.Series(
        [0.3 + (row % 7) / 10 for row in range(24)], dtype="Float64"
    )
    samples.index = range(100, 124)
    samples.attrs = {"source_timezone_status": "caller_supplied_unverified"}
    return samples


@pytest.fixture
def forbid_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Invalid input reached RandomForestRegressor.fit().")

    # 자료형 변환이나 라이브러리 내부 검사에 의존하지 않고 먼저 거부해야 한다.
    monkeypatch.setattr(RandomForestRegressor, "fit", fail_if_called)


def test_returns_fitted_forest_with_default_settings(train_samples: pd.DataFrame) -> None:
    model = train_random_forest(train_samples)

    assert isinstance(model, RandomForestRegressor)
    assert len(model.estimators_) == 200
    assert model.max_depth == 10
    assert model.min_samples_leaf == 5
    assert model.max_features == 0.8
    assert model.bootstrap is True
    assert model.random_state == 42
    assert model.n_jobs == 1
    assert model.n_features_in_ == 7


def test_applies_custom_complexity_settings(train_samples: pd.DataFrame) -> None:
    model = train_random_forest(
        train_samples, n_estimators=7, max_depth=3, min_samples_leaf=2
    )
    assert len(model.estimators_) == 7
    assert model.min_samples_leaf == 2
    assert all(tree.get_depth() <= 3 for tree in model.estimators_)


def test_selects_only_features_in_contract_order(train_samples: pd.DataFrame) -> None:
    train_samples["station_id"] = "22101"
    train_samples["observed_at"] = pd.date_range(
        "2025-01-01", periods=len(train_samples), freq="h", tz="UTC"
    )
    reordered = train_samples.loc[:, train_samples.columns[::-1]]
    model = train_random_forest(reordered, n_estimators=7)
    assert model.feature_names_in_.tolist() == list(FEATURE_COLUMNS)


def test_fit_receives_paired_rows_without_scaling(
    train_samples: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}
    original_fit = RandomForestRegressor.fit

    def capture_fit(self, X, y, *args, **kwargs):
        captured["X"] = X.copy()
        captured["y"] = y.copy()
        return original_fit(self, X, y, *args, **kwargs)

    monkeypatch.setattr(RandomForestRegressor, "fit", capture_fit)
    train_random_forest(train_samples, n_estimators=7)
    assert_frame_equal(
        captured["X"], train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    )
    pd.testing.assert_series_equal(
        captured["y"], train_samples[TARGET_COLUMN].astype("float64")
    )


def test_input_and_attrs_are_preserved(train_samples: pd.DataFrame) -> None:
    original = train_samples.copy(deep=True)
    train_random_forest(train_samples, n_estimators=7)
    assert_frame_equal(train_samples, original)
    assert train_samples.attrs == original.attrs


def test_fixed_seed_reproduces_predictions(train_samples: pd.DataFrame) -> None:
    X = train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    first = train_random_forest(train_samples, n_estimators=7)
    second = train_random_forest(train_samples, n_estimators=7)
    np.testing.assert_array_equal(first.predict(X), second.predict(X))


def test_prediction_is_average_of_tree_predictions(train_samples: pd.DataFrame) -> None:
    X = train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    model = train_random_forest(train_samples, n_estimators=7)

    # 개별 트리는 내부 학습 배열과 같은 형태로 입력을 받는다.
    tree_input = X.to_numpy(dtype="float32")
    expected = np.mean([tree.predict(tree_input) for tree in model.estimators_], axis=0)
    predicted = model.predict(X)
    np.testing.assert_allclose(predicted, expected)
    assert predicted.shape == (len(X),)
    assert np.isfinite(predicted).all()


def test_prediction_does_not_refit_or_replace_trees(
    train_samples: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = train_random_forest(train_samples, n_estimators=7)
    X = train_samples.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    predictions_before = model.predict(X)
    trees_before = tuple(id(tree) for tree in model.estimators_)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Prediction must not call fit().")

    monkeypatch.setattr(model, "fit", fail_if_called)
    for tree in model.estimators_:
        monkeypatch.setattr(tree, "fit", fail_if_called)
    np.testing.assert_array_equal(model.predict(X), predictions_before)
    assert tuple(id(tree) for tree in model.estimators_) == trees_before


@pytest.mark.parametrize("invalid", [None, [], pd.Series([1.0])])
def test_rejects_non_dataframe(invalid: object, forbid_fit: None) -> None:
    with pytest.raises(TypeError, match="pandas DataFrame"):
        train_random_forest(invalid)


def test_rejects_empty_samples(train_samples: pd.DataFrame, forbid_fit: None) -> None:
    with pytest.raises(ValueError, match="No training samples"):
        train_random_forest(train_samples.iloc[:0])


@pytest.mark.parametrize("name", PARAMETER_NAMES)
@pytest.mark.parametrize("value", [True, False, "10", 1.5, None])
def test_rejects_invalid_parameter_types(
    train_samples: pd.DataFrame, name: str, value: object, forbid_fit: None
) -> None:
    with pytest.raises(TypeError, match=f"{name} must be an integer"):
        train_random_forest(train_samples, **{name: value})


@pytest.mark.parametrize("name", PARAMETER_NAMES)
@pytest.mark.parametrize("value", [0, -1])
def test_rejects_invalid_parameter_values(
    train_samples: pd.DataFrame, name: str, value: int, forbid_fit: None
) -> None:
    with pytest.raises(ValueError, match=f"{name} must be greater than zero"):
        train_random_forest(train_samples, **{name: value})


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_rejects_missing_columns(
    train_samples: pd.DataFrame, column: str, forbid_fit: None
) -> None:
    with pytest.raises(ValueError, match="Missing training columns"):
        train_random_forest(train_samples.drop(columns=column))


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("kind", ["string", "boolean", "complex"])
def test_rejects_invalid_numeric_dtype_before_fit(
    train_samples: pd.DataFrame, column: str, kind: str, forbid_fit: None
) -> None:
    dtype = {"string": "string", "boolean": "bool", "complex": "complex128"}[kind]
    train_samples[column] = train_samples[column].astype(dtype)
    with pytest.raises(TypeError, match=f"{column} must have a real numeric dtype"):
        train_random_forest(train_samples)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("value", [pd.NA, float("nan")])
def test_rejects_missing_values_before_fit(
    train_samples: pd.DataFrame, column: str, value: object, forbid_fit: None
) -> None:
    train_samples.loc[100, column] = value
    with pytest.raises(ValueError, match=f"{column} must not contain missing values"):
        train_random_forest(train_samples)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_rejects_infinity_before_fit(
    train_samples: pd.DataFrame, column: str, value: float, forbid_fit: None
) -> None:
    train_samples.loc[100, column] = value
    with pytest.raises(ValueError, match=f"{column} must contain finite values"):
        train_random_forest(train_samples)
