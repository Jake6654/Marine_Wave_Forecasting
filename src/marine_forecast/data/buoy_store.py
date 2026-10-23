import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from marine_forecast.data.buoy_api import (
    SOURCE_COLUMNS,
    SOURCE_TIMEZONE,
    RawBuoyObservation,
)

def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("Timestamp must be a datetime.")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone.")

    # 같은 순간이 항상 같은 DB 키와 비교 형식을 갖게 한다.
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )

def _validate_station_id(station_id: str) -> None:
    """관측소 한 곳을 식별하는 문자열인지 확인한다."""
    if not isinstance(station_id, str):
        raise TypeError("station_id must be a string.")

    if (
        not station_id.isascii()
        or not station_id.isdigit()
        or station_id.strip("0") == ""
    ):
        raise ValueError("Invalid station_id.")

def _encode_observation(
        observation: RawBuoyObservation,
) -> tuple[str, str, str]:
  if not isinstance(observation, RawBuoyObservation):
        raise TypeError("Expected RawBuoyObservation.")

  _validate_station_id(observation.station_id)
  observed_at = _utc_text(observation.observed_at)

  fields = observation.raw_fields

  if (
      not isinstance(fields, tuple)
      or len(fields) != len(SOURCE_COLUMNS)
      or not all(isinstance(value, str) for value in fields)
  ):
      raise ValueError("Invalid raw observation fields.")

  if (
      observation.observed_at.second != 0
      or observation.observed_at.microsecond != 0
  ):
      raise ValueError("Observation time must use minute precision.")

  expected_source_time = observation.observed_at.astimezone(
      SOURCE_TIMEZONE
  ).strftime("%Y%m%d%H%M")

  # 객체의 식별 정보와 원본 필드가 다른 상태를 저장하지 않는다.
  if (
      fields[0] != expected_source_time
      or fields[1] != observation.station_id
  ):
      raise ValueError("Raw fields do not match observation keys.")

  raw_json = json.dumps(
      fields,
      ensure_ascii=False,
      separators=(",", ":"),
  )

  return observation.station_id, observed_at, raw_json


class BuoyObservationStore:
    """관측소, 관측시각별로 최신 수신 내용을 저장한다."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def initialize(self) -> None:
        """필요한 경우에만 DB 파일과 테이블을 생성한다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # closing은 연결을 닫고, 안쪽 with는 트랜잭션을 관리한다.
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS buoy_observations (
                        station_id TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        raw_fields_json TEXT NOT NULL,
                        first_received_at TEXT NOT NULL,
                        last_received_at TEXT NOT NULL,
                        PRIMARY KEY (station_id, observed_at)
                    )
                    """
                )
    def _connect(self) -> sqlite3.Connection:
        """초기화된 DB의 연결을 반환한다."""
        # 파일이 없을 때 빈 DB를 암묵적으로 생성하지 않는다.
        return sqlite3.connect(
            f"{self.path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )

    def save(
        self,
        observations: list[RawBuoyObservation],
        *,
        received_at: datetime,
    ) -> int:
        """관측 목록을 저장하고 처리한 입력 건수를 반환한다."""
        if not isinstance(observations, list):
            raise TypeError("observations must be a list.")

        received_text = _utc_text(received_at)
        rows: list[tuple[str, str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        # 모든 입력을 먼저 검증해 잘못된 입력의 부분 저장을 막는다.
        for observation in observations:
            station_id, observed_at, raw_json = _encode_observation(
                observation
            )
            key = (station_id, observed_at)

            if key in seen:
                raise ValueError("Duplicate observation in save batch.")

            seen.add(key)

            rows.append(
                (
                    station_id,
                    observed_at,
                    raw_json,
                    received_text,
                    received_text,
                )
            )

        if not rows:
            return 0

        # 마지막에 DB 연결을 닫는다
        with closing(self._connect()) as connection:
            # SQL 실행 중 실패하면 묶음 전체를 롤백한다.
            with connection:
                # 성공하면 commit, if fails -> rollback
                connection.executemany(
                    """
                    INSERT INTO buoy_observations (
                        station_id,
                        observed_at,
                        raw_fields_json,
                        first_received_at,
                        last_received_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (station_id, observed_at)
                    DO UPDATE SET
                        raw_fields_json = excluded.raw_fields_json,
                        last_received_at = excluded.last_received_at
                    WHERE excluded.last_received_at
                        >= buoy_observations.last_received_at
                    """,
                    rows,
                )

        return len(rows)

    def load(
        self,
        *,
        station_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawBuoyObservation]:
        """지정한 기간의 관측을 UTC 시각순으로 복원한다."""
        _validate_station_id(station_id)
        start_text = _utc_text(start)
        end_text = _utc_text(end)

        if start_text > end_text:
            raise ValueError("start must not be later than end.")

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT station_id, observed_at, raw_fields_json
                FROM buoy_observations
                WHERE station_id = ?
                  AND observed_at >= ?
                  AND observed_at <= ?
                ORDER BY observed_at
                """,
                (station_id, start_text, end_text),
            ).fetchall()

        observations: list[RawBuoyObservation] = []

        for saved_station, saved_time, raw_json in rows:
            fields = json.loads(raw_json)

            if not isinstance(fields, list):
                raise ValueError("Stored raw fields must be a JSON array.")

            observation = RawBuoyObservation(
                station_id=saved_station,
                observed_at=datetime.fromisoformat(saved_time),
                raw_fields=tuple(fields),
            )

            # 저장 후 손상된 레코드를 정상 관측으로 반환하지 않는다.
            _encode_observation(observation)
            observations.append(observation)

        return observations
    