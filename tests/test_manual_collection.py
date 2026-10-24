"""실제 HTTP 앱과 임시 SQLite로 수동 수집 인증·잠금·저장을 검증한다."""

import json
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from marine_forecast.api.app import create_app
from marine_forecast.api.collection import ManualCollection
from marine_forecast.api.service import ApiError
from marine_forecast.api.settings import ApiSettings
from marine_forecast.collector_runtime import exclusive_collector_lock
from marine_forecast.data.buoy_api import SOURCE_COLUMNS, SOURCE_TIMEZONE, parse_buoy_response
from marine_forecast.data.buoy_client import BuoyApiError
from marine_forecast.data.buoy_store import BuoyObservationStore
import marine_forecast.api.collection as collection_module


NOW = datetime(2026, 10, 24, 8, 10, tzinfo=timezone.utc)
TOKEN = "synthetic-test-token-" * 3
ENDPOINT = "/v1/stations/22101/observations/collect"


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("KMA_API_KEY", "synthetic-secret")
    return ApiSettings(db_path=tmp_path / "observations.sqlite3", collection_token=TOKEN)


def fake_client_factory(key, *, timeout_seconds):
    assert key == "synthetic-secret"
    assert timeout_seconds == 15
    class Client:
        def fetch(self, *, station_id, start, end):
            fields = dict.fromkeys(SOURCE_COLUMNS, "0")
            fields.update(TM=end.astimezone(SOURCE_TIMEZONE).strftime("%Y%m%d%H%M"),
                          STN=station_id, WH_SIG=".5", WS1="2", PA="1018", AQC="raw", MQC="raw")
            return parse_buoy_response("\n".join([
                "#START7777", ",".join([*(fields[name] for name in SOURCE_COLUMNS), "="]), "#7777END",
            ]))
    return Client()


@pytest.fixture
def fake_upstream(monkeypatch):
    original = collection_module.collect_observations
    def wrapped(**kwargs):
        return original(**kwargs, client_factory=fake_client_factory)
    monkeypatch.setattr(collection_module, "collect_observations", wrapped)


def test_authorized_http_collection_saves_into_same_db_and_refreshes_observation(configured, fake_upstream):
    with TestClient(create_app(configured, clock=lambda: NOW)) as client:
        result = client.post(ENDPOINT, headers={"X-Collection-Token": TOKEN})
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "collected" and body["received_count"] == 1
        assert "db_path" not in body
        assert TOKEN not in result.text and "synthetic-secret" not in result.text
        observation = client.get("/v1/stations/22101/observations/latest").json()
        assert observation["status"] == "available"
        assert observation["observation"]["significant_wave_height_m"] == .5
        assert client.get("/v1/stations/22101/predictions/latest").status_code == 503


@pytest.mark.parametrize("token", ["", "wrong", "synthetic-secret"])
def test_wrong_or_missing_token_cannot_create_db(configured, token):
    with TestClient(create_app(configured)) as client:
        response = client.post(ENDPOINT, headers={"X-Collection-Token": token})
    assert response.status_code == 403
    assert not configured.db_path.exists()


def test_missing_token_configuration_disables_collection(tmp_path):
    with TestClient(create_app(ApiSettings(db_path=tmp_path / "missing.sqlite3"))) as client:
        assert client.post(ENDPOINT).json()["error"]["code"] == "collection_not_configured"


def test_missing_key_is_sanitized(configured, monkeypatch):
    monkeypatch.delenv("KMA_API_KEY")
    controller = ManualCollection(configured)
    with pytest.raises(ApiError) as error:
        controller.collect("22101", TOKEN)
    assert error.value.code == "collection_key_missing"
    assert not configured.db_path.exists()


def test_cooldown_is_shared_across_controller_instances(configured, fake_upstream):
    ManualCollection(configured, clock=lambda: NOW).collect("22101", TOKEN)
    with pytest.raises(ApiError) as error:
        ManualCollection(configured, clock=lambda: NOW + timedelta(seconds=29)).collect("22101", TOKEN)
    assert error.value.status_code == 429
    result = ManualCollection(configured, clock=lambda: NOW + timedelta(seconds=31)).collect("22101", TOKEN)
    assert result["received_count"] == 1
    store = BuoyObservationStore(configured.db_path)
    assert len(store.load(station_id="22101", start=NOW - timedelta(hours=1), end=NOW + timedelta(minutes=1))) == 1


def test_periodic_collector_lock_blocks_manual_request(configured):
    with exclusive_collector_lock(configured.db_path.with_suffix(".collector.lock")):
        with pytest.raises(ApiError) as error:
            ManualCollection(configured).collect("22101", TOKEN)
    assert error.value.status_code == 409


def test_upstream_failure_hides_secrets_and_releases_lock(configured, monkeypatch):
    def broken(**kwargs):
        raise BuoyApiError("authKey=PRIVATE")
    monkeypatch.setattr(collection_module, "collect_observations", broken)
    controller = ManualCollection(configured, clock=lambda: NOW)
    with pytest.raises(ApiError) as error:
        controller.collect("22101", TOKEN)
    assert error.value.status_code == 502
    assert "PRIVATE" not in error.value.message
    assert not controller._lock.locked()
    assert not configured.db_path.exists()
    assert json.loads(configured.db_path.with_suffix(".manual-collection.json").read_text())["last_attempt_epoch"] == NOW.timestamp()


def test_concurrent_click_does_not_start_a_second_collection(configured, monkeypatch):
    entered, finish = Event(), Event()
    errors = []
    def slow(**kwargs):
        entered.set()
        assert finish.wait(timeout=5)
        raise BuoyApiError("Synthetic failure")
    monkeypatch.setattr(collection_module, "collect_observations", slow)
    controller = ManualCollection(configured, clock=lambda: NOW)
    def first():
        try:
            controller.collect("22101", TOKEN)
        except ApiError as error:
            errors.append(error.code)
    thread = Thread(target=first)
    thread.start()
    try:
        assert entered.wait(timeout=5)
        with pytest.raises(ApiError) as error:
            controller.collect("22101", TOKEN)
        assert error.value.status_code == 409
    finally:
        finish.set()
        thread.join(timeout=5)
    assert errors == ["collection_upstream_failed"]


def test_collection_token_is_validated_and_not_in_settings_repr():
    assert TOKEN not in repr(ApiSettings(collection_token=TOKEN))
    assert ApiSettings.from_env({"MARINE_COLLECTION_TOKEN": TOKEN}).collection_token == TOKEN
    with pytest.raises(ValueError):
        ApiSettings(collection_token="short")
