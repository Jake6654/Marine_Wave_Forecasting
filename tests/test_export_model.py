"""실제 과거 데이터로 고정 학습 구간과 재로딩 일치를 검증한다."""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from marine_forecast.model_artifact import load_model_artifact


ROOT = Path(__file__).resolve().parents[1]


def test_export_uses_only_frozen_training_window(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("export_model_under_test", ROOT / "scripts/export_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_train = module.train_random_forest
    received = []

    def capture_training(samples):
        received.append(samples.copy())
        return original_train(samples)

    monkeypatch.setattr(module, "train_random_forest", capture_training)
    output = tmp_path / "export"
    result = module.export_model(module.DEFAULT_DATA, output)
    assert len(received) == 1
    train = received[0]
    assert len(train) == 3891
    boundary = pd.Timestamp("2025-10-01T00:00:00+09:00")
    assert train["observed_at"].lt(boundary).all()
    assert train["target_at"].lt(boundary).all()
    assert result["reload_predictions_identical"] is True
    assert result["validation_samples"] == 1130
    bundle = load_model_artifact(output, trusted=True)
    assert bundle.metadata["provenance"]["test_re_evaluated"] is False
    assert bundle.model.get_params()["n_estimators"] == 200
    assert bundle.model.get_params()["max_depth"] == 10
    assert bundle.model.get_params()["min_samples_leaf"] == 5
    assert bundle.model.get_params()["max_features"] == .8
    assert bundle.model.get_params()["random_state"] == 42
    with pytest.raises(FileExistsError):
        module.export_model(module.DEFAULT_DATA, output)
