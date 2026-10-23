"""고정한 학습 구간·설정으로 모델을 저장하고 재로딩 예측을 대조한다."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from marine_forecast.baseline import predict_persistence
from marine_forecast.dataset import (
    attach_targets, load_processed_data, select_complete_samples, split_by_time,
)
from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN, build_features
from marine_forecast.metrics import evaluate_regression
from marine_forecast.model_artifact import load_model_artifact, save_model_artifact
from marine_forecast.training import train_random_forest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT = ROOT / "artifacts/rf_22101_v1"
DEFAULT_DATA = ROOT / "data/processed/buoy_22101_2025_kst_to_utc.csv"


def export_model(data_path: Path, output: Path) -> dict[str, object]:
    """최종 테스트를 재평가하거나 학습에 합치지 않고 산출물을 만든다."""
    if output.exists():
        raise FileExistsError("Artifact already exists; choose another output directory.")
    observations, metadata = load_processed_data(data_path)
    if set(observations["station_id"].unique()) != {"22101"}:
        raise ValueError("Expected observations for station 22101 only.")
    if metadata.get("source_timezone") != "Asia/Seoul" or metadata.get("source_timezone_status") != "confirmed":
        raise ValueError("Expected the KST-confirmed preparation artifact.")
    samples, _ = select_complete_samples(attach_targets(build_features(observations), observations))
    boundaries = {
        "validation_start": "2025-10-01T00:00:00+09:00",
        "test_start": "2025-12-01T00:00:00+09:00",
        "test_end": "2026-01-01T00:00:00+09:00",
    }
    splits, _ = split_by_time(samples, **boundaries)
    train, validation = splits["train"], splits["validation"]
    if train.empty or validation.empty:
        raise ValueError("Training and validation samples must not be empty.")
    # 검증·테스트 데이터는 fit에 전달하지 않는다.
    model = train_random_forest(train)
    inputs = validation.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    predicted = pd.Series(model.predict(inputs), index=inputs.index, dtype="float64")
    scores = evaluate_regression(validation[TARGET_COLUMN], predicted)
    baseline = evaluate_regression(validation[TARGET_COLUMN], predict_persistence(inputs))
    manifest = save_model_artifact(model, output, provenance={
        "station_id": "22101", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "processed_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "source_sha256": metadata["source_sha256"],
        "source_timezone_status": metadata["source_timezone_status"],
        "source_timezone_confirmation_method": metadata.get("source_timezone_confirmation_method"),
        "boundaries": boundaries, "training_samples": len(train),
        "training_first_observed_at": train["observed_at"].min().isoformat(),
        "training_last_target_at": train["target_at"].max().isoformat(),
        "validation_samples": len(validation), "validation_metrics": scores,
        "validation_persistence_metrics": baseline, "test_re_evaluated": False,
    })
    # 방금 이 프로세스가 만든 로컬 산출물만 명시적으로 신뢰한다.
    restored = load_model_artifact(output, trusted=True)
    np.testing.assert_array_equal(model.predict(inputs), restored.model.predict(inputs))
    return {
        "model_id": manifest["model_id"], "training_samples": len(train),
        "validation_samples": len(validation), "validation_metrics": scores,
        "reload_predictions_identical": True, "artifact_path": str(output.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the frozen RandomForest model.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    print(json.dumps(export_model(args.data, args.output), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
