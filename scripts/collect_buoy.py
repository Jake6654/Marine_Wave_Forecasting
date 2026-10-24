"""기상청 부이 관측을 한 번 수집하고 SQLite에 저장한다."""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from marine_forecast.data.buoy_client import (
    BuoyApiClient,
    BuoyApiError,
)
from marine_forecast.data.buoy_collection import collect_observations


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
    """공유 수집 함수를 호출하며 기존 CLI·테스트 진입점을 유지한다."""
    return collect_observations(
        api_key=api_key, db_path=db_path, station_id=station_id,
        lookback_hours=lookback_hours, client_factory=BuoyApiClient,
        now=lambda tz: datetime.now(tz),
    )


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
