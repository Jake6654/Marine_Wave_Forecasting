import math
import pandas as pd

def _validate_values(values: pd.Series, *, name: str) -> None:
    """평가에 사용할 Series 기본 조건을 검사한다"""
    if not isinstance(values, pd.Series):
        raise TypeError(f"{name} must be a pandas Series.")

    if values.empty:
        raise ValueError(f"{name} must not be empty.")

    # 중복 라벨로 표본 대응 관계가 모호해지는 상황을 거부한다.
    if not values.index.is_unique:
        raise ValueError(f"{name} index must be unique.")

    dtype = values.dtype

    if (
        not pd.api.types.is_numeric_dtype(dtype)
        or pd.api.types.is_bool_dtype(dtype)
        or pd.api.types.is_complex_dtype(dtype)
    ):
        raise TypeError(f"{name} must have a real numeric dtype.")

    # pandas의 평균 계산이 결측값을 건너뛰지 못하도록 먼저 거부한다.
    if values.isna().any():
        raise ValueError(f"{name} must not contain missing values.")

    if values.isin([float("inf"), float("-inf")]).any():
        raise ValueError(f"{name} must contain finite values.")


def evaluate_regression(
        y_true: pd.Series,
        y_pred: pd.Series,
) -> dict[str, float]:
    """같은 표본의 실제값과 예측값으로 MAE and RMSE를 계산한다."""

    _validate_values(y_true, name="y_true")
    _validate_values(y_pred, name="y_pred")

    # 다른 표본끼리 라벨 정렬되어 계산되는 실수를 막는다.
    if not y_true.index.equals(y_pred.index):
        raise ValueError(
            "y_true and y_pred must have identical indexes in the same order."
        )

    actual = y_true.astype("float64")
    predicted = y_pred.astype("float64")

    errors = predicted - actual

    # 평균 절대 오차
    mae = float(errors.abs().mean())
    # 평균 제곱근 오차(큰 오차에 더 큰 영향을 주기 위해서 제곱근을 한다)
    rmse = math.sqrt(float(errors.pow(2).mean()))

    # 입력이 유한해도 극단적인 값의 계산은 넘칠 수 있다.
    if not math.isfinite(mae) or not math.isfinite(rmse):
        raise ValueError("Metric calculation produced a non-finite result.")

    return {
        "mae_m": mae,
        "rmse_m": rmse,
    }