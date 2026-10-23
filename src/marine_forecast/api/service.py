"""HTTP에 의존하지 않는 조회 서비스와 안전한 공개 오류를 정의한다."""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pandas as pd

from marine_forecast.api.settings import ApiSettings
from marine_forecast.data.buoy_api import SOURCE_TIMEZONE
from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.data.live_normalization import POLICY_VERSION, SOURCE_WARNINGS, normalize_live_observations
from marine_forecast.inference import predict_latest
from marine_forecast.model_artifact import ModelBundle


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ForecastService:
    def __init__(
        self, settings: ApiSettings, bundle: ModelBundle | None, *,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.settings = settings
        self.bundle = bundle
        self.store = BuoyObservationStore(settings.db_path)
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Service clock must return an aware datetime.")
        return now.astimezone(timezone.utc)

    def _station(self, station_id: str) -> None:
        if station_id != "22101":
            raise ApiError(404, "station_not_supported", "Only station 22101 is supported.")

    def _load_recent(self, station_id: str, now: datetime):
        try:
            # 조회 서비스는 테이블을 초기화하거나 빈 DB를 생성하지 않는다.
            return self.store.load(station_id=station_id, start=now - timedelta(hours=24), end=now)
        except (OSError, sqlite3.Error):
            raise ApiError(503, "observation_store_unavailable", "Observation storage is unavailable.") from None
        except (ValueError, TypeError, KeyError):
            raise ApiError(503, "observation_store_invalid", "Stored observations are invalid.") from None

    def latest_observation(self, station_id: str) -> dict[str, object]:
        self._station(station_id)
        now = self._now()
        rows = self._load_recent(station_id, now)
        result = {
            "status": "unavailable", "reason": "no_recent_observations",
            "station_id": station_id, "generated_at_utc": now,
            "policy_version": POLICY_VERSION, "warnings": list(SOURCE_WARNINGS),
        }
        if not rows:
            return result
        # 관측 조회는 예측 입력과 달리 최신 30분 관측도 그대로 보여준다.
        frame, report = normalize_live_observations([rows[-1]])
        row = frame.iloc[0]
        value_fields = ["significant_wave_height_m", "wind_speed_mps", "api_pressure_hpa"]
        missing = [name for name in value_fields if pd.isna(row[name])]
        age = (now - row["observed_at"].to_pydatetime()).total_seconds() / 60
        stale = age > self.settings.max_age_minutes
        # 오래됨과 결측이 동시에 있어도 missing_fields와 is_stale에 둘 다 보존한다.
        result.update({
            "status": "stale" if stale else "incomplete" if missing else "available",
            "reason": "stale_observation" if stale else "missing_observation_values" if missing else None,
            "observation_age_minutes": age, "is_stale": stale, "missing_fields": missing,
            "normalization_report": report,
            "observation": {
                "observed_at_utc": row["observed_at"],
                "observed_at_kst": row["observed_at"].tz_convert(SOURCE_TIMEZONE),
                **{name: None if pd.isna(row[name]) else float(row[name]) for name in value_fields},
                "aqc_raw": row["aqc_raw"], "mqc_raw": row["mqc_raw"],
                "is_hourly": row["observed_at"].minute == 0,
            },
        })
        return result

    def latest_prediction(self, station_id: str) -> dict[str, object]:
        self._station(station_id)
        if self.bundle is None:
            raise ApiError(503, "model_unavailable", "A trusted compatible model is not loaded.")
        try:
            # QC 허용 여부는 요청자가 아닌 서버 설정에서만 결정한다.
            return predict_latest(
                self.store, self.bundle, station_id=station_id, now=self._now(),
                max_age_minutes=self.settings.max_age_minutes,
                allow_unverified_source=self.settings.allow_unverified_source,
            )
        except (OSError, sqlite3.Error):
            raise ApiError(503, "observation_store_unavailable", "Observation storage is unavailable.") from None
        except (ValueError, TypeError, KeyError):
            raise ApiError(503, "prediction_input_invalid", "Prediction inputs or model contract are invalid.") from None

    def ready(self) -> dict[str, str]:
        if self.bundle is None:
            raise ApiError(503, "model_unavailable", "A trusted compatible model is not loaded.")
        self._load_recent("22101", self._now())
        # 준비 상태는 모델·저장소 접근성이지 자료 최신성이나 QC 인증이 아니다.
        return {"status": "ready"}
