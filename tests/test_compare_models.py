import hashlib
import importlib.util
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/compare_models.py"


@pytest.fixture
def experiment() -> ModuleType:
    spec = importlib.util.spec_from_file_location("comparison_test_experiment", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_local_data(experiment: ModuleType) -> tuple[Path, Path]:
    paths = (experiment.DATA_PATH, experiment.DATA_PATH.with_suffix(".metadata.json"))
    if not all(path.is_file() for path in paths):
        pytest.skip("연간 정제 CSV와 메타데이터가 로컬에 없어 검증하지 못함")
    return paths


def hashes(paths: tuple[Path, Path]) -> dict[Path, str]:
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_real_three_model_validation_integration(
    experiment: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """학습·예측·평가가 모두 같은 시간 분할과 표본을 사용하는지 검증한다."""
    paths = require_local_data(experiment)
    before = hashes(paths)
    captured = {"models": {}, "training_samples": {}, "evaluations": []}
    original_split = experiment.split_by_time
    original_evaluate = experiment.evaluate_regression

    def capture_split(*args, **kwargs):
        result = original_split(*args, **kwargs)
        captured["splits"] = result[0]
        return result

    def trainer_wrapper(name, original_train):
        def capture_train(samples, **kwargs):
            captured["training_samples"][name] = samples.copy(deep=True)
            model = original_train(samples, **kwargs)
            monkeypatch.setattr(model, "predict", Mock(wraps=model.predict))

            def fail_if_refitted(*args, **kwargs):
                raise AssertionError("Prediction must not refit the model.")

            # 실제 학습 뒤에는 다시 학습하지 않고 검증 예측만 해야 한다.
            monkeypatch.setattr(model, "fit", fail_if_refitted)
            captured["models"][name] = model
            if name == "Ridge":
                captured["ridge_mean_before"] = model.named_steps["scaler"].mean_.copy()
                captured["ridge_variance_before"] = model.named_steps["scaler"].var_.copy()
                captured["ridge_coefficients_before"] = model.named_steps["ridge"].coef_.copy()
            else:
                captured["forest_trees_before"] = tuple(id(tree) for tree in model.estimators_)
            return model

        return capture_train

    def capture_evaluate(actual, predicted):
        scores = original_evaluate(actual, predicted)
        captured["evaluations"].append((actual.copy(), predicted.copy(), scores))
        return scores

    monkeypatch.setattr(experiment, "split_by_time", capture_split)
    monkeypatch.setattr(experiment, "train_ridge", trainer_wrapper("Ridge", experiment.train_ridge))
    monkeypatch.setattr(
        experiment, "train_random_forest",
        trainer_wrapper("RandomForest", experiment.train_random_forest),
    )
    monkeypatch.setattr(experiment, "evaluate_regression", capture_evaluate)

    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        returncode = experiment.main()

    assert returncode == 0
    train = captured["splits"]["train"]
    validation = captured["splits"]["validation"]
    assert (len(train), len(validation)) == (3891, 1130)
    assert train["target_at"].lt(pd.Timestamp("2025-10-01T00:00:00Z")).all()
    assert validation["observed_at"].ge(pd.Timestamp("2025-10-01T00:00:00Z")).all()
    assert validation["target_at"].lt(pd.Timestamp("2025-12-01T00:00:00Z")).all()
    assert validation.attrs["source_timezone_status"] == "caller_supplied_unverified"

    X_train = train.loc[:, list(experiment.FEATURE_COLUMNS)].astype("float64")
    X_validation = validation.loc[:, list(experiment.FEATURE_COLUMNS)].astype("float64")
    for name, model in captured["models"].items():
        assert_frame_equal(captured["training_samples"][name], train)
        assert model.feature_names_in_.tolist() == list(experiment.FEATURE_COLUMNS)
        assert model.predict.call_count == 1
        assert_frame_equal(model.predict.call_args.args[0], X_validation)

    ridge = captured["models"]["Ridge"]
    scaler = ridge.named_steps["scaler"]
    assert scaler.n_samples_seen_ == 3891
    np.testing.assert_allclose(scaler.mean_, X_train.mean().to_numpy())
    np.testing.assert_allclose(scaler.var_, X_train.var(ddof=0).to_numpy())
    np.testing.assert_array_equal(scaler.mean_, captured["ridge_mean_before"])
    np.testing.assert_array_equal(scaler.var_, captured["ridge_variance_before"])
    np.testing.assert_array_equal(
        ridge.named_steps["ridge"].coef_, captured["ridge_coefficients_before"]
    )
    forest = captured["models"]["RandomForest"]
    assert len(forest.estimators_) == 200
    assert tuple(id(tree) for tree in forest.estimators_) == captured["forest_trees_before"]

    # 평가 호출은 세 번뿐이며 모두 검증 정답을 사용해야 한다.
    assert len(captured["evaluations"]) == 3
    for actual, predicted, scores in captured["evaluations"]:
        assert_series_equal(actual, validation[experiment.TARGET_COLUMN])
        assert predicted.index.equals(validation.index)
        assert len(predicted) == 1130
        errors = [float(prediction - truth) for prediction, truth in zip(predicted, actual)]
        assert scores == pytest.approx({
            "mae_m": math.fsum(abs(error) for error in errors) / len(errors),
            "rmse_m": math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
        })
    assert_series_equal(
        captured["evaluations"][0][1],
        validation["significant_wave_height_m"].astype("float64"),
        check_names=False,
    )
    output = capsys.readouterr().out
    assert "Training samples: 3891" in output
    assert "Validation samples: 1130" in output
    assert all(f"{name}: MAE=" in output for name in ("Persistence", "Ridge", "RandomForest"))
    assert output.count("MAE=") == 3
    assert hashes(paths) == before


def test_comparison_cli_integration(experiment: ModuleType, tmp_path: Path) -> None:
    """프로젝트 밖에서 직접 실행해도 파일 경로와 패키지 연결이 유지돼야 한다."""
    paths = require_local_data(experiment)
    before = hashes(paths)
    completed = subprocess.run(
        [sys.executable, "-B", "-I", str(SCRIPT_PATH)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Training samples: 3891" in completed.stdout
    assert "Validation samples: 1130" in completed.stdout
    assert "Source timezone is not confirmed" in completed.stderr
    assert all(
        f"{name}: MAE=" in completed.stdout
        for name in ("Persistence", "Ridge", "RandomForest")
    )
    assert completed.stdout.count("MAE=") == 3
    assert hashes(paths) == before
