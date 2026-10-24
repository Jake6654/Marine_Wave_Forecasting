"""백그라운드 수집의 수명주기·실패 격리·SQLite 연결을 검증한다."""

import hashlib
import json
import logging
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marine_forecast.api.app import create_app
from marine_forecast.api.background_collection import BackgroundCollection, INTERVAL_SECONDS
from marine_forecast.api.settings import ApiSettings
from marine_forecast.collector_runtime import exclusive_collector_lock
from marine_forecast.data.buoy_api import SOURCE_COLUMNS, SOURCE_TIMEZONE, parse_buoy_response
from marine_forecast.data.buoy_collection import collect_observations
from marine_forecast.model_artifact import load_model_artifact


MODULE = "marine_forecast.api.background_collection"
ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "deployment/models/rf_22101_v1"
NOW = datetime(2026, 10, 24, 9, 10, tzinfo=timezone.utc)


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("KMA_API_KEY", "test-key-not-real")
    return BackgroundCollection(ApiSettings(db_path=tmp_path / "observations.sqlite3"))


def test_background_collection_is_opt_in_and_boolean():
    assert not ApiSettings.from_env({}).background_collection
    assert ApiSettings.from_env({"MARINE_API_BACKGROUND_COLLECTION": "true"}).background_collection
    with pytest.raises(ValueError):
        ApiSettings.from_env({"MARINE_API_BACKGROUND_COLLECTION": "yes"})
    with pytest.raises(TypeError):
        ApiSettings(background_collection="false")


def test_missing_key_fails_before_thread_start(worker, monkeypatch):
    monkeypatch.delenv("KMA_API_KEY")
    with pytest.raises(ValueError, match="KMA_API_KEY"):
        worker.start()
    assert worker._thread is None


def test_collection_uses_api_db_and_twelve_hours(worker, monkeypatch):
    seen = []
    monkeypatch.setattr(f"{MODULE}.collect_observations", lambda **kw: seen.append(kw) or {"received_count": 24})
    assert worker.collect_once()
    assert seen == [{"api_key": "test-key-not-real", "db_path": worker.settings.db_path,
                     "station_id": "22101", "lookback_hours": 12}]
    # 이전 시도가 끝나면 다른 수집기도 같은 잠금을 얻을 수 있다.
    with exclusive_collector_lock(worker.settings.db_path.with_suffix(".collector.lock")):
        pass


def test_existing_collector_prevents_overlapping_fetch(worker, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.collect_observations", lambda **kw: pytest.fail("Must not fetch"))
    with exclusive_collector_lock(worker.settings.db_path.with_suffix(".collector.lock")):
        assert not worker.collect_once()


def test_failure_preserves_db_and_redacts_exception(worker, monkeypatch, caplog):
    worker.settings.db_path.write_bytes(b"existing-db")

    def fail(**kw):
        raise RuntimeError("private-test-key-in-url")

    monkeypatch.setattr(f"{MODULE}.collect_observations", fail)
    with caplog.at_level(logging.WARNING):
        assert not worker.collect_once()
    assert "private-test-key-in-url" not in caplog.text
    assert worker.settings.db_path.read_bytes() == b"existing-db"
    with exclusive_collector_lock(worker.settings.db_path.with_suffix(".collector.lock")):
        pass


def test_loop_waits_after_failure_and_retries_without_sleeping(worker, monkeypatch):
    attempts = []

    class FakeStop:
        def is_set(self):
            return False

        def wait(self, seconds):
            assert seconds == INTERVAL_SECONDS == 600
            return len(attempts) == 2

    worker._stop = FakeStop()
    monkeypatch.setattr(worker, "collect_once", lambda: attempts.append(1) or False)
    worker._run()
    assert len(attempts) == 2


def test_first_collection_is_immediate_and_stop_interrupts_wait(worker, monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(worker, "collect_once", lambda: called.set() or True)
    worker.start()
    try:
        assert called.wait(5)
        with pytest.raises(RuntimeError, match="already"):
            worker.start()
    finally:
        worker.stop()
    assert not worker._thread.is_alive()


def test_default_api_does_not_start_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(BackgroundCollection, "start", lambda self: pytest.fail("Opt-in only"))
    with TestClient(create_app(ApiSettings(db_path=tmp_path / "a.sqlite3"))) as client:
        assert client.get("/health/live").status_code == 200


def test_enabled_api_manages_worker_lifecycle(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(BackgroundCollection, "start", lambda self: events.append("start"))
    monkeypatch.setattr(BackgroundCollection, "stop", lambda self: events.append("stop"))
    with TestClient(create_app(ApiSettings(db_path=tmp_path / "a.sqlite3", background_collection=True))) as client:
        assert events == ["start"]
        assert client.get("/health/live").status_code == 200
    assert events == ["start", "stop"]


def test_slow_collection_does_not_block_http_startup(worker, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def pending(self):
        entered.set()
        release.wait(5)
        return False

    monkeypatch.setattr(BackgroundCollection, "collect_once", pending)
    settings = replace(worker.settings, background_collection=True)
    with TestClient(create_app(settings)) as client:
        try:
            assert entered.wait(5)
            assert not release.is_set()
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 503
        finally:
            release.set()


def test_packaged_model_checksum_and_environment():
    metadata = json.loads((MODEL / "metadata.json").read_text())
    digest = hashlib.sha256((MODEL / "model.pkl").read_bytes()).hexdigest()
    assert digest == metadata["model_sha256"]
    assert metadata["model_id"] == "rf-22101-0c2a284dede20560"
    assert load_model_artifact(MODEL, trusted=True).model.n_estimators == 200


def test_collection_model_and_http_integration(worker, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, **kwargs):
            rows = []
            for hours in range(8):
                at = NOW.replace(minute=0) - timedelta(hours=hours)
                fields = dict.fromkeys(SOURCE_COLUMNS, "0")
                fields.update(TM=at.astimezone(SOURCE_TIMEZONE).strftime("%Y%m%d%H%M"),
                              STN="22101", WH_SIG=".4", WS1="4.7", PA="1017.3")
                rows.append(",".join([*(fields[name] for name in SOURCE_COLUMNS), "="]))
            return parse_buoy_response("\n".join(["#START7777", *rows, "#7777END"]))

    def fake_network(**kw):
        return collect_observations(**kw, client_factory=FakeClient, now=lambda tz: NOW.astimezone(tz))

    monkeypatch.setattr(f"{MODULE}.collect_observations", fake_network)
    assert worker.collect_once()
    settings = replace(worker.settings, model_path=MODEL, trust_local_model=True, allow_unverified_source=True)
    with TestClient(create_app(settings, clock=lambda: NOW)) as client:
        assert client.get("/health/ready").status_code == 200
        observation = client.get("/v1/stations/22101/observations/latest").json()
        assert observation["observation"]["significant_wave_height_m"] == .4
        prediction = client.get("/v1/stations/22101/predictions/latest").json()
        assert prediction["status"] == "experimental"
        assert prediction["model_id"] == "rf-22101-0c2a284dede20560"
        assert prediction["operational_use_allowed"] is False
