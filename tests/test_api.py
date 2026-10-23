"""실제 ASGI 앱·임시 SQLite·저장 모델을 HTTP 테스트 클라이언트로 연결한다."""

import importlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestRegressor

from marine_forecast.api.app import create_app
from marine_forecast.api.schemas import ErrorResponse, ObservationResponse
from marine_forecast.api.service import ApiError, ForecastService
from marine_forecast.api.settings import ApiSettings
from marine_forecast.data.buoy_api import SOURCE_COLUMNS, SOURCE_TIMEZONE, parse_buoy_response
from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.features import FEATURE_COLUMNS
from marine_forecast.model_artifact import save_model_artifact


NOW = datetime(2026, 10, 24, 0, 40, tzinfo=timezone.utc)
OBS = "/v1/stations/22101/observations/latest"
PRED = "/v1/stations/22101/predictions/latest"


def make_row(at, **changes):
    fields = dict.fromkeys(SOURCE_COLUMNS, "0")
    fields.update(TM=at.astimezone(SOURCE_TIMEZONE).strftime("%Y%m%d%H%M"), STN="22101",
                  WH_SIG=".5", WS1="2", PA="1018", AQC="00000000000000//000", MQC="---------------")
    fields.update(changes)
    return parse_buoy_response("\n".join([
        "#START7777", ",".join([*(fields[name] for name in SOURCE_COLUMNS), "="]), "#7777END",
    ]))[0]


@pytest.fixture
def resources(tmp_path):
    store = BuoyObservationStore(tmp_path / "observations.sqlite3")
    store.initialize()
    store.save([make_row(NOW.replace(minute=0) - timedelta(hours=h)) for h in range(8)], received_at=NOW)
    inputs = pd.DataFrame({name: [0., 1., 2.] for name in FEATURE_COLUMNS})
    model = RandomForestRegressor(n_estimators=5, random_state=42).fit(inputs, [.5, .5, .5])
    path = tmp_path / "model"
    save_model_artifact(model, path, provenance={"station_id": "22101"})
    return store, ApiSettings(db_path=store.path, model_path=path, trust_local_model=True,
                              allow_unverified_source=True)


def client_for(settings, **kwargs):
    return TestClient(create_app(settings, clock=lambda: NOW), **kwargs)


def test_http_observation_and_prediction_use_different_latest_timestamps(resources):
    store, settings = resources
    store.save([make_row(NOW.replace(minute=30), WH_SIG=".7")], received_at=NOW)
    with client_for(settings) as client:
        response = client.get(OBS)
        assert response.status_code == 200
        body = response.json()
        ObservationResponse.model_validate(body)
        assert body["status"] == "available"
        assert body["observation"]["significant_wave_height_m"] == .7
        assert body["observation"]["is_hourly"] is False
        assert body["observation_age_minutes"] == 10
        assert body["quality_status"] == "unverified"
        assert body["operational_use_allowed"] is False
        assert response.headers["cache-control"] == "no-store"
        response = client.get(PRED)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "experimental"
        assert result["predicted_significant_wave_height_m"] == pytest.approx(.5)
        assert datetime.fromisoformat(result["observed_at_kst"]).hour == 9
        assert datetime.fromisoformat(result["target_at_kst"]).hour == 15
        assert result["operational_use_allowed"] is False
        assert result["reason"] is None


def test_policy_is_server_controlled_and_cannot_be_enabled_by_query(resources):
    _, settings = resources
    with client_for(replace(settings, allow_unverified_source=False)) as client:
        response = client.get(PRED)
        assert response.status_code == 200
        assert response.json()["reason"] == "source_policy_unverified"
        assert "predicted_significant_wave_height_m" not in response.json()
        assert client.get(PRED + "?allow_unverified_source=true").status_code == 422
        assert client.get(OBS).json()["status"] == "available"


def test_missing_values_are_null_not_zero(resources):
    store, settings = resources
    store.save([make_row(NOW.replace(minute=0), WH_SIG="-99", WS1="nan")], received_at=NOW + timedelta(seconds=1))
    with client_for(settings) as client:
        body = client.get(OBS).json()
        assert body["status"] == "incomplete"
        assert body["observation"]["significant_wave_height_m"] is None
        assert body["observation"]["wind_speed_mps"] is None
        assert set(body["missing_fields"]) == {"significant_wave_height_m", "wind_speed_mps"}
        assert len(body["normalization_report"]["issues"]) == 2
        prediction = client.get(PRED).json()
        assert prediction["reason"] == "incomplete_latest_features"
        assert "predicted_significant_wave_height_m" not in prediction
        json.dumps(body, allow_nan=False)


def test_stale_observation_returns_values_but_no_prediction(resources):
    _, settings = resources
    app = create_app(settings, clock=lambda: NOW + timedelta(hours=2))
    with TestClient(app) as client:
        body = client.get(OBS).json()
        assert body["status"] == "stale"
        assert body["is_stale"] is True
        assert body["observation"]["significant_wave_height_m"] == .5
        assert client.get(PRED).json()["reason"] == "stale_observation"
        assert client.get("/health/ready").status_code == 200


def test_empty_db_is_a_business_unavailable_result(resources, tmp_path):
    _, settings = resources
    empty = BuoyObservationStore(tmp_path / "empty.sqlite3")
    empty.initialize()
    with client_for(replace(settings, db_path=empty.path)) as client:
        assert client.get(OBS).json()["reason"] == "no_recent_observations"
        assert client.get(PRED).json()["reason"] == "no_recent_observations"
        assert client.get("/health/ready").status_code == 200


def test_missing_db_is_not_implicitly_created(resources, tmp_path):
    _, settings = resources
    missing = tmp_path / "not_created.sqlite3"
    with client_for(replace(settings, db_path=missing)) as client:
        for endpoint in (OBS, PRED, "/health/ready"):
            response = client.get(endpoint)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "observation_store_unavailable"
        assert client.get("/health/live").status_code == 200
    assert not missing.exists()


def test_untrusted_model_is_never_unpickled(resources, monkeypatch):
    _, settings = resources
    module = importlib.import_module("marine_forecast.api.app")
    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted model must not be loaded.")
    monkeypatch.setattr(module, "load_model_artifact", forbidden)
    with client_for(replace(settings, trust_local_model=False)) as client:
        assert client.get(OBS).status_code == 200
        assert client.get(PRED).status_code == 503
        assert client.get("/health/ready").status_code == 503


def test_model_is_loaded_once_per_lifespan_and_cleaned_up(resources, monkeypatch):
    _, settings = resources
    module = importlib.import_module("marine_forecast.api.app")
    original = module.load_model_artifact
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "load_model_artifact", counted)
    app = create_app(settings, clock=lambda: NOW)
    assert not calls
    with TestClient(app) as client:
        assert client.get(PRED).status_code == client.get(PRED).status_code == 200
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert len(calls) == 1
    assert not hasattr(app.state, "forecast_service")


def test_model_loading_failure_does_not_leak_private_details(resources, monkeypatch, caplog):
    _, settings = resources
    module = importlib.import_module("marine_forecast.api.app")
    def fail(*args, **kwargs):
        raise ValueError("private-path-and-authKey=SECRET")
    monkeypatch.setattr(module, "load_model_artifact", fail)
    with client_for(settings) as client:
        response = client.get(PRED)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_unavailable"
        assert "SECRET" not in response.text + caplog.text
        assert client.get(OBS).status_code == 200


@pytest.mark.parametrize("path,code", [
    ("/v1/stations/99999/predictions/latest", 404),
    ("/v1/stations/abc/observations/latest", 422),
    ("/unknown", 404),
    (OBS + "?token=SECRET", 422),
])
def test_http_errors_share_a_sanitized_envelope(resources, path, code):
    with client_for(resources[1]) as client:
        response = client.get(path)
        assert response.status_code == code
        ErrorResponse.model_validate(response.json())
        assert "SECRET" not in response.text


def test_write_methods_are_not_accepted(resources):
    with client_for(resources[1]) as client:
        response = client.post(PRED)
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"


def test_unexpected_error_is_a_generic_500(resources, monkeypatch, caplog):
    def broken(self, station_id):
        raise RuntimeError("SECRET private sqlite path")
    monkeypatch.setattr(ForecastService, "latest_observation", broken)
    with client_for(resources[1], raise_server_exceptions=False) as client:
        response = client.get(OBS)
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert response.headers["cache-control"] == "no-store"
        assert "SECRET" not in response.text + caplog.text


def test_openapi_exposes_success_and_error_contracts(resources):
    with client_for(resources[1]) as client:
        schema = client.get("/openapi.json").json()
        operation = schema["paths"]["/v1/stations/{station_id}/predictions/latest"]["get"]
        assert {"200", "404", "422", "503", "500"}.issubset(operation["responses"])
        assert "ErrorResponse" in schema["components"]["schemas"]
        assert "oneOf" in operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_service_rejects_naive_clock(resources):
    service = ForecastService(resources[1], None, clock=lambda: NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="aware"):
        service.latest_observation("22101")


def test_service_rejects_unsupported_station_before_accessing_store(resources):
    service = ForecastService(resources[1], None)
    with pytest.raises(ApiError) as error:
        service.latest_observation("99999")
    assert error.value.status_code == 404


def test_corrupt_db_returns_sanitized_503(resources, tmp_path):
    _, settings = resources
    damaged = tmp_path / "private.sqlite3"
    damaged.write_bytes(b"SECRET not a SQLite database")
    with client_for(replace(settings, db_path=damaged)) as client:
        response = client.get(OBS)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "observation_store_unavailable"
        assert "SECRET" not in response.text and "private.sqlite3" not in response.text


def test_corrupt_model_keeps_observation_endpoint_working(resources):
    _, settings = resources
    (settings.model_path / "model.pkl").write_bytes(b"broken model")
    with client_for(settings) as client:
        assert client.get(OBS).status_code == 200
        assert client.get(PRED).status_code == 503


def test_half_hour_only_data_is_visible_but_cannot_be_used_for_prediction(resources, tmp_path):
    _, settings = resources
    store = BuoyObservationStore(tmp_path / "half-hour.sqlite3")
    store.initialize()
    store.save([make_row(NOW.replace(minute=30))], received_at=NOW)
    with client_for(replace(settings, db_path=store.path)) as client:
        assert client.get(OBS).json()["status"] == "available"
        assert client.get(PRED).json()["reason"] == "no_hourly_observations"


def test_stale_and_missing_values_are_both_exposed(resources):
    store, settings = resources
    store.save([make_row(NOW.replace(minute=0), WH_SIG="-99")], received_at=NOW + timedelta(seconds=1))
    with TestClient(create_app(settings, clock=lambda: NOW + timedelta(hours=2))) as client:
        body = client.get(OBS).json()
        assert body["status"] == "stale"
        assert body["is_stale"] is True
        assert body["missing_fields"] == ["significant_wave_height_m"]
        assert body["observation"]["significant_wave_height_m"] is None


def test_read_requests_do_not_write_or_collect(resources, monkeypatch):
    store, settings = resources
    monkeypatch.delenv("KMA_API_KEY", raising=False)
    def snapshot():
        with sqlite3.connect(store.path) as connection:
            return connection.execute("SELECT * FROM buoy_observations ORDER BY observed_at").fetchall()
    before = snapshot()
    with client_for(settings) as client:
        assert client.get(OBS).status_code == client.get(PRED).status_code == 200
    assert snapshot() == before


def test_future_observation_is_never_exposed(resources):
    store, settings = resources
    store.save([make_row(NOW.replace(minute=0) + timedelta(hours=1), WH_SIG="99")], received_at=NOW)
    with client_for(settings) as client:
        body = client.get(OBS).json()
        assert body["observation"]["significant_wave_height_m"] == .5
