"""CLI와 HTTP가 공유하는 기상청 조회·저장·재조회 대조 로직."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from marine_forecast.data.buoy_api import SOURCE_TIMEZONE
from marine_forecast.data.buoy_client import BuoyApiClient
from marine_forecast.data.buoy_store import BuoyObservationStore


def collect_observations(
    *, api_key: str, db_path: str | Path, station_id: str,
    lookback_hours: int = 12, client_factory: Callable = BuoyApiClient,
    now: Callable[[tzinfo], datetime] = datetime.now,
) -> dict[str, object]:
    """최근 자료를 가져와 API가 사용하는 DB에 저장하고 원문 일치를 확인한다."""
    if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int):
        raise TypeError("lookback_hours must be an integer.")
    if not 1 <= lookback_hours <= 24:
        raise ValueError("lookback_hours must be between 1 and 24.")
    end = now(SOURCE_TIMEZONE).replace(second=0, microsecond=0)
    start = end - timedelta(hours=lookback_hours)
    client = client_factory(api_key, timeout_seconds=15.0)
    observations = client.fetch(station_id=station_id, start=start, end=end)
    if not observations:
        raise ValueError("No observations returned by the client.")
    received_at = now(timezone.utc)
    # 네트워크 실패 시 빈 DB를 만들지 않고, 수신 성공 후에만 초기화한다.
    store = BuoyObservationStore(db_path)
    store.initialize()
    processed_count = store.save(observations, received_at=received_at)
    restored = store.load(station_id=station_id, start=start, end=end)
    restored_by_key = {(row.station_id, row.observed_at): row for row in restored}
    for row in observations:
        if restored_by_key.get((row.station_id, row.observed_at)) != row:
            raise ValueError("Stored observation does not match the received data.")
    latest = max(observations, key=lambda row: row.observed_at)
    return {
        "station_id": station_id, "query_start_kst": start.isoformat(),
        "query_end_kst": end.isoformat(), "received_at_utc": received_at.isoformat(),
        "received_count": len(observations), "processed_count": processed_count,
        "stored_window_count": len(restored),
        "latest_observed_at_kst": latest.observed_at.astimezone(SOURCE_TIMEZONE).isoformat(),
        "db_path": str(store.path),
    }
