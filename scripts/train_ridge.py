"""Ridge를 학습하고 검증 구간에서 기준 모델과 비교한다."""

from pathlib import Path

import pandas as pd

from marine_forecast.baseline import predict_persistence
from marine_forecast.dataset import (
    attach_targets,
    load_processed_data,
    select_complete_samples,
    split_by_time,
)
from marine_forecast.features import (
    FEATURE_COLUMNS,
    HORIZON_HOURS,
    TARGET_COLUMN,
    build_features,
)
from marine_forecast.metrics import evaluate_regression
from marine_forecast.training import train_ridge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "buoy_22101_2025_kst_to_utc.csv"
)


def main() -> int:
  """데이터 준비, 학습, 검증 비교를 실행"""
  observations, metadata = load_processed_data(DATA_PATH)
  features = build_features(observations)
  dataset = attach_targets(features, observations)
  complete_samples, _ = select_complete_samples(dataset)


  splits, _ = split_by_time(
        complete_samples,
        # 한국 달력 기준 경계를 지정하고, 분할 함수에서 UTC로 변환한다.
        validation_start="2025-10-01T00:00:00+09:00",
        test_start="2025-12-01T00:00:00+09:00",
        test_end="2026-01-01T00:00:00+09:00",
    )

  train = splits["train"]
  validation = splits["validation"]

  # 검증 테스트 정답은 모델 학습에 전달하지 않는다
  model = train_ridge(train, alpha=1.0)

  X_validation = validation.loc[
    :, list(FEATURE_COLUMNS)
  ].astype("float64")
  y_validation = validation[TARGET_COLUMN]

  baseline_predictions = predict_persistence(X_validation)

  # 예측 배열에는 인덱스가 없으므로 원본 검증 인덱스를 붙인다.
  ridge_predictions = pd.Series(
    model.predict(X_validation),
    index = X_validation.index,
    name = (
      f"predicted_significant_wave_height_{HORIZON_HOURS}h_m"
    ),
    dtype="float64"
  )

  baseline_scores = evaluate_regression(
        y_validation,
        baseline_predictions,
    )
  ridge_scores = evaluate_regression(
        y_validation,
        ridge_predictions,
    )

  print("Training samples:", len(train))
  print("Validation samples:", len(validation))
  print(
        "Source timezone status:",
        metadata["source_timezone_status"],
    )

  for name, scores in (
      ("Persistence", baseline_scores),
      ("Ridge", ridge_scores),
  ):
    print(
        f"{name}: "
        f"MAE={scores['mae_m']:.6f}m, "
        f"RMSE={scores['rmse_m']:.6f}m"
    )

  print("\nCoefficients for standardized features:")
  coefficients = pd.Series(
      model.named_steps["ridge"].coef_,
      index=list(FEATURE_COLUMNS),
      name="coefficient",
  )
  print(coefficients.to_string())

  return 0


if __name__ == "__main__":
    raise SystemExit(main())


