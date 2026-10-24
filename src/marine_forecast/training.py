"""준비된 학습 표본으로 회귀 모델을 학습한다."""

import math

import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestRegressor
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

      # 같은 표에서 입력과 정답을 선택해 행의 대응 관계를 유지한다
      X_train = train_samples.loc[
          :, list(FEATURE_COLUMNS)
      ].astype("float64")
      y_train = train_samples[TARGET_COLUMN].astype("float64")

      model = Pipeline(
          steps=[
              ("scaler", StandardScaler()),
              ("ridge",  Ridge(alpha=float(alpha))),
          ]
      )

      # 학습 구간으로만 표준화 기준과 회귀 계수를 학습한다
      model.fit(X_train, y_train)

      # 결국 함수가 반환하는 것은 예측값이 아니라 학습 상태를 가진 Pipeline 객체이다
      return model

def train_random_forest(
    train_samples: pd.DataFrame,
    *,
    n_estimators: int=200, # 숲을 구성할 결정 트리 갯수
    max_depth: int=10, # 각 트리가 조건을 나눠 내려갈 수 있는 최대 깊이
    min_samples_leaf: int=5,
) -> RandomForestRegressor:
    """준비된 학습 표본으로 RandomForest 회귀를 학습한다."""
    if not isinstance(train_samples, pd.DataFrame):
        raise TypeError("train_samples must be a pandas DataFrame.")

    if train_samples.empty:
        raise ValueError("No training samples.")

    # 잘못된 복잡도 설정으로 학습을 시작하지 않는다
    for name, value in (
        ("n_estimators", n_estimators),
        ("max_depth", max_depth),
        ("min_samples_leaf", min_samples_leaf),
    ):
        
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer.")

        if value <= 0:
            raise ValueError(f"{name} must be greater than zero.")

    # 모델 입력 7개와 학습 정답 1개를 필수 열로 정한다
    # 파이썬에서는 + 연산자로 리스트를 합칠 수 있다.
    required_columns = list(FEATURE_COLUMNS) + [TARGET_COLUMN]
    # [
    # "significant_wave_height_m",
    # "wind_speed_mps",
    # "station_pressure_hpa",
    # "wave_height_lag_1h_m",
    # "wave_height_lag_3h_m",
    # "wave_height_lag_6h_m",
    # "wave_height_change_1h_m",
    # "target_significant_wave_height_6h_m",
    # ]
    missing_columns = set(required_columns).difference(
        train_samples.columns
    )

    if missing_columns:
        raise ValueError(
            f"Missing training columns: {sorted(missing_columns)}"
        )

    for column in required_columns:
        # 현재 검사할 열을 Series로 선택한다
        values = train_samples[column]
        dtype = values.dtype

        # 다른 모델과 동일한 입력 품질 조건을 적용한다
        if (
            not pd.api.types.is_numeric_dtype(dtype)
            or pd.api.types.is_bool_dtype(dtype)
            or pd.api.types.is_complex_dtype(dtype)
        ):
            raise TypeError(
                f"{column} must have a real numeric dtype."
            )

        if values.isna().any():
            raise ValueError(
                f"{column} must not contain missing values."
            )

        if values.isin([float("inf"), float("-inf")]).any():
            raise ValueError(
                f"{column} must contain finite values."
            )

    # 모든 열의 검사가 끝난 뒤 입력과 정답을 선택한다
    # 열 순서도 FEATURE_COLUMNS에 정의된 순서로 고정한다
    X_train = train_samples.loc[
        #: 모든 행 선택 
        :, list(FEATURE_COLUMNS) # 입력 7열만 선택
    ].astype("float64") # 실수형으로 통일

    # 같은 행의 실제 6시간 뒤 파고를 정답 Series 로 선택한다
    y_train = train_samples[TARGET_COLUMN].astype("float64")

    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        max_features=0.8,
        bootstrap=True,
        random_state=42,
        n_jobs=1,
    )

    # 검증 데이터는 전달하지 않고 학습 구간으로만 트리를 구성한다.
    model.fit(X_train, y_train)

    return model