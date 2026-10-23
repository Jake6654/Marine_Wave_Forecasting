"""명시적 허용 시에만 실제 API→DB→모델 예측을 검증한다."""

import importlib.util
import os
from pathlib import Path

import pytest

from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.inference import predict_latest
from marine_forecast.model_artifact import load_model_artifact


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("RUN_LIVE_BUOY_TESTS") != "1", reason="Live API test is opt-in.")
def test_real_api_to_db_to_experimental_forecast(tmp_path):
    key = os.environ.get("KMA_API_KEY")
    if not key:
        pytest.skip("KMA_API_KEY is not configured.")
    artifact = ROOT / "artifacts/rf_22101_v1"
    if not artifact.is_dir():
        pytest.skip("Run scripts/export_model.py to create a trusted local model first.")
    spec = importlib.util.spec_from_file_location("collect_for_prediction", ROOT / "scripts/collect_buoy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "live.sqlite3"
    module.collect_once(api_key=key, db_path=database, station_id="22101")
    result = predict_latest(
        BuoyObservationStore(database), load_model_artifact(artifact, trusted=True),
        allow_unverified_source=True,
    )
    assert result["status"] == "experimental"
    assert result["predicted_significant_wave_height_m"] >= 0
    assert result["operational_use_allowed"] is False
    assert result["quality_status"] == "unverified"
