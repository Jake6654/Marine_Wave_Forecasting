"""기상청 부이 관측을 한 번 수집하고 SQLite에 저장한다."""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marine_forecast.data.buoy_api import SOURCE_TIMEZONE
from marine_forecast.data.buoy_client import (
    BuoyApiClient,
    BuoyApiError,
)
from marine_forecast.data.buoy_store import BuoyObservationStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "runtime"
    / "buoy_observations.sqlite3"
)


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    """명령행에서 수집 대상과 저장 경로를 읽는다."""
    parser = argparse.ArgumentParser(
        description="Collect buoy observations once and save to SQLite.",
    )
    parser.add_argument(
        "--station-id",
        default="22101",
        help="Buoy station ID. Default: 22101.",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=12,
        help="Hours to collect, from 1 to 24. Default: 12.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database.",
    )

    return parser.parse_args(argv)


def collect_once(
    *,
    api_key: str,
    db_path: str | Path,
    station_id: str,
    lookback_hours: int = 12,
) -> dict[str, object]:
    """최근 관측을 조회·저장하고 결과 요약을 반환한다."""
    if (
        isinstance(lookback_hours, bool)
        or not isinstance(lookback_hours, int)
    ):
        raise TypeError("lookback_hours must be an integer.")

    if not 1 <= lookback_hours <= 24:
        raise ValueError("lookback_hours must be between 1 and 24.")

    # API 입력의 정밀도에 맞춰 초와 마이크로초를 제거한다.
    end = datetime.now(SOURCE_TIMEZONE).replace(
        second=0,
        microsecond=0,
    )
    start = end - timedelta(hours=lookback_hours)

    client = BuoyApiClient(
        api_key,
        timeout_seconds=15.0,
    )

    observations = client.fetch(
        station_id=station_id,
        start=start,
        end=end,
    )

    if not observations:
        raise ValueError("No observations returned by the client.")

    # 관측시각과 구분해 이번 호출의 수신 완료시각을 기록한다.
    received_at = datetime.now(timezone.utc)

    # API 조회 실패 시 불필요한 빈 DB를 만들지 않도록 뒤에서 초기화한다.
    store = BuoyObservationStore(db_path)
    store.initialize()

    processed_count = store.save(
        observations,
        received_at=received_at,
    )

    restored = store.load(
        station_id=station_id,
        start=start,
        end=end,
    )

    restored_by_key = {
        (observation.station_id, observation.observed_at): observation
        for observation in restored
    }

    # 건수뿐 아니라 이번 수신 관측의 실제 필드가 저장됐는지 대조한다.
    for observation in observations:
        key = (
            observation.station_id,
            observation.observed_at,
        )

        if restored_by_key.get(key) != observation:
            raise ValueError(
                "Stored observation does not match the received data."
            )

    latest = max(
        observations,
        key=lambda observation: observation.observed_at,
    )

    return {
        "station_id": station_id,
        "query_start_kst": start.isoformat(),
        "query_end_kst": end.isoformat(),
        "received_at_utc": received_at.isoformat(),
        "received_count": len(observations),
        "processed_count": processed_count,
        "stored_window_count": len(restored),
        "latest_observed_at_kst": latest.observed_at.astimezone(
            SOURCE_TIMEZONE
        ).isoformat(),
        "db_path": str(store.path),
    }


def main() -> int:
    """설정을 읽고 수집을 실행한 뒤 종료 코드를 반환한다."""
    args = parse_args()
    api_key = os.environ.get("KMA_API_KEY")

    if not api_key:
        print(
            "ERROR: KMA_API_KEY is not configured.",
            file=sys.stderr,
        )
        return 1

    try:
        report = collect_once(
            api_key=api_key,
            db_path=args.db,
            station_id=args.station_id,
            lookback_hours=args.lookback_hours,
        )
    except (
        BuoyApiError,
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ) as error:
        # client가 정리한 오류만 출력하고 인증키나 요청 URL은 출력하지 않는다.
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("Station:", report["station_id"])
    print("Query start KST:", report["query_start_kst"])
    print("Query end KST:", report["query_end_kst"])
    print("Received observations:", report["received_count"])
    print("Processed observations:", report["processed_count"])
    print(
        "Stored observations in query window:",
        report["stored_window_count"],
    )
    print("Latest observation KST:", report["latest_observed_at_kst"])
    print("Received at UTC:", report["received_at_utc"])
    print("Database:", report["db_path"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())