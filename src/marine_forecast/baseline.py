import pandas as pd

from marine_forecast.features import HORIZON_HOURS


CURRENT_WAVE_COLUMN: str = "significant_wave_height_m"


def predict_persistence(features: pd.DataFrame) -> pd.Series:
    """현재 유의파고를 미래 예측값으로 반환한다"""

    if not isinstance(features, pd.DataFrame):
        raise TypeError("features must be a pandas DataFrame.")

    if CURRENT_WAVE_COLUMN not in features.columns:
        raise ValueError(
            f"Missing feature column: {CURRENT_WAVE_COLUMN}"
        )

    if features.empty:
        raise ValueError("No feature rows to predict.")

    # 각 표본과 예측값의 대응 관계를 명확하게 유지한다.
    if not features.index.is_unique:
        raise ValueError("Feature index must be unique.")

    current_wave = features[CURRENT_WAVE_COLUMN]
    dtype = current_wave.dtype

    # 참·거짓이나 복소수를 파고 수치로 해석하지 않는다.
    if (
        not pd.api.types.is_numeric_dtype(dtype)
        or pd.api.types.is_bool_dtype(dtype)
        or pd.api.types.is_complex_dtype(dtype)
    ):
        raise TypeError("Current wave height must have a real numeric dtype.")

    # 데이터가 없다는 사실을 임의의 예측값으로 대체하지 않는다.
    if current_wave.isna().any():
        raise ValueError("Current wave height must not be missing")

    if current_wave.isin([float("inf"), float("-inf")]).any():
        raise ValueError("Current wave height must be finite.")

    if current_wave.lt(0).any():
        raise ValueError("Current wave height must be non-negative.")

    # 값을 학습하지 않고 현재 파고를 그대로 미래 예측으로 사용한다.
    predictions = current_wave.astype("float64").copy()
    predictions.name = (
        f"predicted_significant_wave_height_{HORIZON_HOURS}h_m"
    )

    return predictions
