"""동일한 검증 표본에서 세 예측 방법을 비교한다."""

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
from marine_forecast.training import (
    train_random_forest,
    train_ridge,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "buoy_22101_2025_kst_to_utc.csv"
)


def main() -> int:
    """데이터 준비 후 학습 모델과 기준 예측을 비교한다."""
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

    X_validation = validation.loc[
        :, list(FEATURE_COLUMNS)
    ].astype("float64")
    y_validation = validation[TARGET_COLUMN]

    # 두 모델은 동일한 학습 표본으로 학습한다.
    models = {
        "Ridge": train_ridge(train, alpha=1.0),
        "RandomForest": train_random_forest(train),
    }

    predictions = {
        "Persistence": predict_persistence(X_validation),
    }

    for name, model in models.items():
        # 예측 배열 순서에 대응하는 검증 인덱스를 복원한다.
        predictions[name] = pd.Series(
            model.predict(X_validation),
            index=X_validation.index,
            name=f"{name}_prediction_{HORIZON_HOURS}h_m",
            dtype="float64",
        )

    print("Training samples:", len(train))
    print("Validation samples:", len(validation))
    print(
        "Source timezone status:",
        metadata["source_timezone_status"],
    )

    for name, predicted in predictions.items():
        # 최종 테스트 구간이 아닌 동일한 검증 정답으로만 평가한다.
        scores = evaluate_regression(
            y_validation,
            predicted,
        )
        print(
            f"{name}: "
            f"MAE={scores['mae_m']:.6f}m, "
            f"RMSE={scores['rmse_m']:.6f}m"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
