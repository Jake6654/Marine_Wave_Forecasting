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
    / "buoy_22101_2025_utc_assumed.csv"
)


def main() -> int:
    """데이터 준비 후 학습 모델과 기준 예측을 비교한다."""
    observations, metadata = load_processed_data(DATA_PATH)
    features = build_features(observations)
    dataset = attach_targets(features, observations)
    complete_samples, _ = select_complete_samples(dataset)

    splits, _ = split_by_time(
        complete_samples,
        validation_start="2025-10-01T00:00:00Z",
        test_start="2025-12-01T00:00:00Z",
        test_end="2026-01-01T00:00:00Z",
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
