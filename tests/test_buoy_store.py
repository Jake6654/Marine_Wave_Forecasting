"""임시 SQLite 파일로 관측 저장과 재조회를 검증한다."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marine_forecast.data.buoy_api import parse_buoy_response
from marine_forecast.data.buoy_store import BuoyObservationStore


START = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
RECEIVED = END + timedelta(minutes=10)


def make_observation(
    *,
    timestamp: str = "202610241700",
    station_id: str = "22101",
    wind: str = "1.4",
):
    fields = [
        timestamp,
        station_id,
        "90",
        wind,
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

    text = "\n".join(
        [
            "#START7777",
            ",".join(fields),
            "#7777END",
        ]
    )

    return parse_buoy_response(text)[0]


@pytest.fixture
def store(tmp_path: Path) -> BuoyObservationStore:
    result = BuoyObservationStore(
        tmp_path / "runtime" / "observations.sqlite3"
    )
    result.initialize()
    return result


def load_station(store: BuoyObservationStore):
    return store.load(
        station_id="22101",
        start=START,
        end=END,
    )


def test_save_and_reload_preserves_observation(
    store: BuoyObservationStore,
) -> None:
    observation = make_observation()

    assert store.save([observation], received_at=RECEIVED) == 1
    assert load_station(store) == [observation]


def test_repeated_save_does_not_duplicate_rows(
    store: BuoyObservationStore,
) -> None:
    observation = make_observation()

    store.save([observation], received_at=RECEIVED)
    store.save(
        [observation],
        received_at=RECEIVED + timedelta(minutes=5),
    )

    assert len(load_station(store)) == 1


def test_newer_receipt_updates_without_old_receipt_overwrite(
    store: BuoyObservationStore,
) -> None:
    original = make_observation(wind="1.4")
    corrected = make_observation(wind="1.6")

    store.save([original], received_at=RECEIVED)
    store.save(
        [corrected],
        received_at=RECEIVED + timedelta(minutes=5),
    )

    # 나중에 처리된 오래된 배치가 최신 수신값을 덮어쓰면 안 된다.
    store.save([original], received_at=RECEIVED)

    assert load_station(store)[0].get_raw("WS1") == "1.6"

    with closing(sqlite3.connect(store.path)) as connection:
        first, last = connection.execute(
            """
            SELECT first_received_at, last_received_at
            FROM buoy_observations
            """
        ).fetchone()

    assert datetime.fromisoformat(first) == RECEIVED
    assert datetime.fromisoformat(last) == (
        RECEIVED + timedelta(minutes=5)
    )


def test_load_filters_station_and_includes_boundaries(
    store: BuoyObservationStore,
) -> None:
    at_start = make_observation(timestamp="202610241600")
    later = make_observation(timestamp="202610241700")
    another_station = make_observation(station_id="22102")

    store.save(
        [later, another_station, at_start],
        received_at=RECEIVED,
    )

    result = store.load(
        station_id="22101",
        start=START,
        end=START,
    )

    assert result == [at_start]


def test_invalid_batch_does_not_partially_save(
    store: BuoyObservationStore,
) -> None:
    valid = make_observation()
    invalid = replace(valid, raw_fields=("broken",))

    with pytest.raises(ValueError):
        store.save([valid, invalid], received_at=RECEIVED)

    assert load_station(store) == []


def test_sql_failure_rolls_back_whole_batch(
    store: BuoyObservationStore,
) -> None:
    # 두 번째 행에서 DB 오류가 발생하도록 합성 조건을 만든다.
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TRIGGER reject_test_station
                BEFORE INSERT ON buoy_observations
                WHEN NEW.station_id = '22102'
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic failure');
                END
                """
            )

    with pytest.raises(sqlite3.IntegrityError):
        store.save(
            [
                make_observation(),
                make_observation(station_id="22102"),
            ],
            received_at=RECEIVED,
        )

    assert load_station(store) == []


def test_rejects_naive_receipt_time(
    store: BuoyObservationStore,
) -> None:
    with pytest.raises(ValueError, match="timezone"):
        store.save(
            [make_observation()],
            received_at=RECEIVED.replace(tzinfo=None),
        )


def test_save_requires_initialized_database(tmp_path: Path) -> None:
    result = BuoyObservationStore(tmp_path / "missing.sqlite3")

    with pytest.raises(sqlite3.OperationalError):
        result.save(
            [make_observation()],
            received_at=RECEIVED,
        )

    assert not result.path.exists()


def test_empty_batch_returns_zero(
    store: BuoyObservationStore,
) -> None:
    assert store.save([], received_at=RECEIVED) == 0
    assert load_station(store) == []