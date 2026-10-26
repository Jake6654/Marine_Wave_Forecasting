"""고정 모델을 2025년 12월 미사용 구간에서 평가하고 결과를 보존한다."""

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from marine_forecast.baseline import predict_persistence
from marine_forecast.dataset import attach_targets, load_processed_data, select_complete_samples, split_by_time
from marine_forecast.features import FEATURE_COLUMNS, TARGET_COLUMN, build_features
from marine_forecast.metrics import evaluate_regression
from marine_forecast.model_artifact import load_model_artifact
from marine_forecast.training import train_random_forest


ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = {
    "validation_start": "2025-10-01T00:00:00+09:00",
    "test_start": "2025-12-01T00:00:00+09:00",
    "test_end": "2026-01-01T00:00:00+09:00",
}
EXPECTED_MODEL_ID = "rf-22101-0c2a284dede20560"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(actual: pd.Series, predicted: pd.Series) -> dict[str, object]:
    """빈 구간은 0점으로 표현하지 않고 N/A와 표본 수 0을 반환한다."""
    if not actual.index.equals(predicted.index):
        raise ValueError("Evaluation indexes must match.")
    if actual.empty:
        return {"samples": 0, "mae_m": None, "rmse_m": None, "bias_m": None,
                "underprediction_count": 0, "underprediction_rate": None,
                "mean_shortfall_when_under_m": None, "mean_shortfall_all_m": None}
    metrics = evaluate_regression(actual, predicted)
    errors = predicted.astype("float64") - actual.astype("float64")
    under = errors.lt(0)
    return {
        "samples": len(actual), **metrics, "bias_m": float(errors.mean()),
        "underprediction_count": int(under.sum()),
        "underprediction_rate": float(under.mean()),
        "mean_shortfall_when_under_m": float(-errors[under].mean()) if under.any() else None,
        "mean_shortfall_all_m": float((-errors).clip(lower=0).mean()),
    }


def evaluate(data: Path, artifact: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError("Evaluation output already exists; do not overwrite the first record.")
    # 본인이 생성한 배포 모델에만 신뢰를 부여하며, 원본 산출물은 수정하지 않는다.
    bundle = load_model_artifact(artifact, trusted=True)
    provenance = bundle.metadata["provenance"]
    if bundle.metadata["model_id"] != EXPECTED_MODEL_ID:
        raise ValueError("Expected the previously selected deployment model.")
    if sha256(data) != provenance["processed_sha256"]:
        raise ValueError("Processed data differs from the frozen model's training source.")
    if provenance["boundaries"] != BOUNDARIES or provenance["training_samples"] != 3891:
        raise ValueError("Frozen training provenance does not match.")

    observations, metadata = load_processed_data(data)
    if (set(observations["station_id"].unique()) != {"22101"}
            or metadata.get("source_timezone") != "Asia/Seoul"
            or metadata.get("source_timezone_status") != "confirmed"
            or metadata.get("source_sha256") != provenance["source_sha256"]):
        raise ValueError("Unexpected station, timezone or raw data source.")
    samples, complete_report = select_complete_samples(attach_targets(build_features(observations), observations))
    splits, split_report = split_by_time(samples, **BOUNDARIES)
    if {name: len(rows) for name, rows in splits.items()} != {"train": 3891, "validation": 1130, "test": 728}:
        raise ValueError("Sample counts differ from the frozen split.")
    train, validation, test = splits["train"], splits["validation"], splits["test"]
    threshold = float(train[TARGET_COLUMN].quantile(.9))
    if threshold != 1.0:
        raise ValueError("Training-derived high-wave threshold changed.")

    # 계획대로 학습 3,891개만 사용해 재현하고, 테스트를 보기 전에 배포 모델과 대조한다.
    reproduced = train_random_forest(train)
    if reproduced.get_params() != bundle.model.get_params():
        raise ValueError("Training implementation no longer matches the frozen settings.")
    validation_inputs = validation.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    np.testing.assert_array_equal(reproduced.predict(validation_inputs), bundle.model.predict(validation_inputs))

    # 테스트 구간에서는 고정한 RF와 현재값 유지 방법만 평가한다. 재선택·튜닝하지 않는다.
    inputs = test.loc[:, list(FEATURE_COLUMNS)].astype("float64")
    actual = test[TARGET_COLUMN].astype("float64")
    rf = pd.Series(bundle.model.predict(inputs), index=inputs.index, dtype="float64")
    np.testing.assert_array_equal(rf.to_numpy(), reproduced.predict(inputs))
    baseline = predict_persistence(inputs)
    high = actual.ge(threshold)
    scores = {
        "overall": {"RandomForest": summarize(actual, rf), "Persistence": summarize(actual, baseline)},
        "high_wave": {"RandomForest": summarize(actual[high], rf[high]),
                      "Persistence": summarize(actual[high], baseline[high])},
    }
    improvement = {}
    for metric in ("mae_m", "rmse_m"):
        base = scores["overall"]["Persistence"][metric]
        improvement[metric] = 100 * (base - scores["overall"]["RandomForest"][metric]) / base if base else None
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": bundle.metadata["model_id"], "processed_sha256": sha256(data),
        "model_sha256": sha256(artifact / "model.pkl"),
        "metadata_sha256": sha256(artifact / "metadata.json"), "uv_lock_sha256": sha256(ROOT / "uv.lock"),
        "environment": {key: bundle.metadata[key] for key in ("python_version", "sklearn_version", "pandas_version")},
        "code_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in [
            ROOT / "scripts/evaluate_final_test.py", ROOT / "src/marine_forecast/features.py",
            ROOT / "src/marine_forecast/dataset.py", ROOT / "src/marine_forecast/metrics.py",
            ROOT / "src/marine_forecast/training.py", ROOT / "src/marine_forecast/baseline.py"]},
        "boundaries_kst": BOUNDARIES, "split_rows": {key: len(value) for key, value in splits.items()},
        "high_wave_threshold_m": threshold, "high_wave_actual_min_m": float(actual[high].min()) if high.any() else None,
        "high_wave_actual_max_m": float(actual[high].max()) if high.any() else None,
        "first_test_observed_at_utc": test["observed_at"].min().isoformat(),
        "last_test_target_at_utc": test["target_at"].max().isoformat(),
        "complete_samples_report": complete_report, "split_report": split_report,
        "training_only_reproduction_matches_artifact": True,
        "test_used_for_model_selection": False, "artifact_modified": False,
        "overall_improvement_percent_vs_persistence": improvement, **scores,
    }
    records = test.loc[:, ["station_id", "observed_at", "target_at"]].copy()
    records["actual_wave_m"] = actual
    records["random_forest_m"] = rf
    records["persistence_m"] = baseline
    records["random_forest_error_m"] = rf - actual
    records["persistence_error_m"] = baseline - actual
    records["high_wave"] = high
    # 완성한 두 결과 파일을 함께 공개하고, 기존 결과나 모델은 덮어쓰지 않는다.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".evaluation-") as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        records.to_csv(stage / "predictions.csv", index=False)
        (stage / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        stage.rename(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the frozen RF on the held-out December 2025 split.")
    parser.add_argument("--data", type=Path, default=ROOT / "data/processed/buoy_22101_2025_kst_to_utc.csv")
    parser.add_argument("--artifact", type=Path, default=ROOT / "deployment/models/rf_22101_v1")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/final_test_2025_12")
    args = parser.parse_args()
    result = evaluate(args.data, args.artifact, args.output)
    print(json.dumps({key: result[key] for key in ["model_id", "split_rows", "overall", "high_wave",
                     "overall_improvement_percent_vs_persistence"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
