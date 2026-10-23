"""가짜 API client와 실제 SQLite로 1회 수집 연결을 검증한다."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marine_forecast.data.buoy_api import (
    SOURCE_TIMEZONE,
    parse_buoy_response,
)
from marine_forecast.data.buoy_client import BuoyApiError


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "collect_buoy.py"
)
FIXED_NOW = datetime(
    2026, 10, 24, 8, 10,
    tzinfo=timezone.utc,
)


@pytest.fixture
def collector(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location(
        "collect_buoy_under_test",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return FIXED_NOW.replace(tzinfo=None)
            return FIXED_NOW.astimezone(tz)

    # 시각을 고정해 실제 실행 시간에 따라 테스트 결과가 달라지지 않게 한다.
    monkeypatch.setattr(module, "datetime", FixedDatetime)

    return module


def make_observations(timestamp: str, station_id: str):
    fields = [
        timestamp,
        station_id,
        "90",
        "1.4",
        "2.0",
        "91",
        "1.5",
        "2.1",
        "1018.0",
        "60.0",
        "15.0",
        "21.0",
        "0.3",
        "0.2",
        "0.2",
        "3.0",
        "180",
        "00000000000000//000",
        "---------------",
        "=",
    ]

    return parse_buoy_response(
        "\n".join(
            [
                "#START7777",
                ",".join(fields),
                "#7777END",
            ]
        )
    )


def test_collects_saves_reloads_and_deduplicates(
    collector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []

    class FakeClient:
        def __init__(self, api_key, *, timeout_seconds):
            assert api_key == "fake-key"
            assert timeout_seconds == 15.0

        def fetch(self, *, station_id, start, end):
            calls.append((station_id, start, end))
            return make_observations(
                end.strftime("%Y%m%d%H%M"),
                station_id,
            )

    monkeypatch.setattr(collector, "BuoyApiClient", FakeClient)

    db_path = tmp_path / "runtime" / "observations.sqlite3"

    first = collector.collect_once(
        api_key="fake-key",
        db_path=db_path,
        station_id="22101",
    )
    second = collector.collect_once(
        api_key="fake-key",
        db_path=db_path,
        station_id="22101",
    )

    assert db_path.is_file()
    assert first["received_count"] == 1
    assert first["processed_count"] == 1
    assert first["stored_window_count"] == 1
    assert second["stored_window_count"] == 1

    assert first["received_at_utc"] == FIXED_NOW.isoformat()
    assert first["latest_observed_at_kst"] == (
        FIXED_NOW.astimezone(SOURCE_TIMEZONE)
        .replace(second=0, microsecond=0)
        .isoformat()
    )

    assert len(calls) == 2
    assert calls[0][0] == "22101"
    assert (
        calls[0][2] - calls[0][1]
    ).total_seconds() == 12 * 3600


def test_api_failure_does_not_create_database(
    collector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, **kwargs):
            raise BuoyApiError("Synthetic API failure.")

    monkeypatch.setattr(
        collector,
        "BuoyApiClient",
        FailingClient,
    )

    db_path = tmp_path / "absent" / "observations.sqlite3"

    with pytest.raises(BuoyApiError):
        collector.collect_once(
            api_key="fake-key",
            db_path=db_path,
            station_id="22101",
        )

    assert not db_path.exists()


@pytest.mark.parametrize("lookback_hours", [0, 25, True])
def test_rejects_invalid_query_window(
    collector,
    tmp_path: Path,
    lookback_hours,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        collector.collect_once(
            api_key="fake-key",
            db_path=tmp_path / "unused.sqlite3",
            station_id="22101",
            lookback_hours=lookback_hours,
        )


def test_main_reports_missing_key_without_collecting(
    collector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("KMA_API_KEY", raising=False)

    args = collector.parse_args(
        ["--db", str(tmp_path / "unused.sqlite3")]
    )
    monkeypatch.setattr(collector, "parse_args", lambda: args)

    def fail_if_called(**kwargs):
        raise AssertionError("Collection must not run without a key.")

    monkeypatch.setattr(
        collector,
        "collect_once",
        fail_if_called,
    )

    assert collector.main() == 1
    assert "KMA_API_KEY is not configured" in capsys.readouterr().err