"""준비된 학습 표본으로 회귀 모델을 학습한다."""

import math

import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN

def train_ridge(
    train_samples: pd.DataFrame,
    *,
    alpha: float = 1.0,
) -> Pipeline:
  """입력 표준화와 Ridge 회귀를 학습한 파이프라인을 반환한다"""

  if not isinstance(train_samples, pd.DataFrame):
        raise TypeError("train_samples must be a pandas DataFrame.")

  if train_samples.empty:
      raise ValueError("No training samples.")

  # 잘못된 설정으로 학습을 시작하지 않도록 먼저 검사한다.
  if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
      raise TypeError("alpha must be a real number.")

  if not math.isfinite(alpha) or alpha <= 0:
      raise ValueError("alpha must be finite and greater than zero.")

  required_columns = list(FEATURE_COLUMNS) + [TARGET_COLUMN]
  missing_columns = set(required_columns).difference(train_samples.columns)


  if missing_columns:
        raise ValueError(
            f"Missing training columns: {sorted(missing_columns)}"
        )

  for column in required_columns:
      values = train_samples[column]
      dtype = values.dtype

      # 숫자 문자열이나 참·거짓을 학습 수치로 해석하지 않는다.
      if (
          not pd.api.types.is_numeric_dtype(dtype)
          or pd.api.types.is_bool_dtype(dtype)
          or pd.api.types.is_complex_dtype(dtype)
      ):
          raise TypeError(
              f"{column} must have a real numeric dtype."
          )

      if values.isna().any():
          raise ValueError(f"{column} must not contain missing values.")

      if values.isin([float("inf"), float("-inf")]).any():
          raise ValueError(f"{column} must contain finite values.")

  # 모든 열의 검사가 끝난 뒤 같은 표에서 입력과 정답을 선택한다.
  X_train = train_samples.loc[
      :, list(FEATURE_COLUMNS)
  ].astype("float64")
  y_train = train_samples[TARGET_COLUMN].astype("float64")

  model = Pipeline(
      steps=[
          ("scaler", StandardScaler()),
          ("ridge", Ridge(alpha=float(alpha))),
      ]
  )

  # 학습 구간으로만 표준화 기준과 회귀 계수를 학습한다.
  model.fit(X_train, y_train)

  return model
