"""학습 모델 파일의 저장·신뢰 경계·입력 계약·손상 탐지를 검증한다."""

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestRegressor

from marine_forecast.features import FEATURE_COLUMNS
from marine_forecast.model_artifact import load_model_artifact, save_model_artifact


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def artifact(tmp_path):
    inputs = pd.DataFrame({name: [0., 1., 2.] for name in FEATURE_COLUMNS})
    model = RandomForestRegressor(n_estimators=5, random_state=42).fit(inputs, [0., 1., 2.])
    path = tmp_path / "artifact"
    save_model_artifact(model, path, provenance={"station_id": "22101"})
    return path, model, inputs


def test_round_trip_predictions_are_identical(artifact):
    path, original, inputs = artifact
    restored = load_model_artifact(path, trusted=True)
    np.testing.assert_array_equal(original.predict(inputs), restored.model.predict(inputs))


def test_pickle_requires_explicit_trust(artifact):
    with pytest.raises(ValueError, match="trusted"):
        load_model_artifact(artifact[0])


def test_export_never_overwrites_existing_artifact(artifact):
    path, model, _ = artifact
    original = (path / "model.pkl").read_bytes()
    with pytest.raises(FileExistsError):
        save_model_artifact(model, path, provenance={"station_id": "22101"})
    assert (path / "model.pkl").read_bytes() == original


@pytest.mark.parametrize("field,value", [
    ("feature_columns", list(reversed(FEATURE_COLUMNS))), ("horizon_hours", 3),
    ("station_id", "99999"), ("schema_version", 99),
    ("sklearn_version", "unknown"), ("python_version", "unknown"),
    ("pandas_version", "unknown"), ("model_id", "wrong"),
    ("model_parameters", {}),
])
def test_metadata_mismatch_is_rejected(artifact, field, value):
    path = artifact[0]
    metadata_path = path / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata[field] = value
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        load_model_artifact(path, trusted=True)


def test_corruption_is_detected_before_unpickling(artifact):
    path = artifact[0]
    (path / "model.pkl").write_bytes(b"not a pickle")
    with pytest.raises(ValueError, match="checksum"):
        load_model_artifact(path, trusted=True)


def test_multi_target_model_is_not_a_single_wave_height_artifact(artifact, tmp_path):
    _, _, inputs = artifact
    model = RandomForestRegressor(n_estimators=2).fit(inputs, [[0., 1.], [1., 2.], [2., 3.]])
    with pytest.raises(ValueError, match="single"):
        save_model_artifact(model, tmp_path / "multi", provenance={"station_id": "22101"})


def test_cli_model_error_is_sanitized_and_nonzero(tmp_path):
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/predict_latest.py"), "--model", str(tmp_path / "missing"),
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert json.loads(result.stderr)["status"] == "error"
    assert "Traceback" not in result.stderr


def test_cli_default_does_not_opt_into_unverified_source(artifact):
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/predict_latest.py"), "--model", str(artifact[0]),
        "--trust-local-model",
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "source_policy_unverified"
