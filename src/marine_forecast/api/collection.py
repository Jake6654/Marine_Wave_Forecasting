"""수동 수집의 인증·상호 배제·재호출 제한을 관리한다."""

import hmac
import json
import math
import os
import sqlite3
import threading
from contextlib import ExitStack
from datetime import datetime, timezone

from marine_forecast.api.service import ApiError, utc_now
from marine_forecast.api.settings import ApiSettings
from marine_forecast.collector_runtime import _write_status, exclusive_collector_lock
from marine_forecast.data.buoy_client import BuoyApiError
from marine_forecast.data.buoy_collection import collect_observations


COOLDOWN_SECONDS = 30


class ManualCollection:
    def __init__(self, settings: ApiSettings, *, clock=utc_now):
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()

    def collect(self, station_id: str, supplied_token: str) -> dict[str, object]:
        expected = self.settings.collection_token
        if expected is None:
            raise ApiError(503, "collection_not_configured", "Manual collection is not configured.")
        if not hmac.compare_digest(expected.encode(), supplied_token.encode()):
            raise ApiError(403, "collection_forbidden", "Manual collection authorization failed.")
        if station_id != "22101":
            raise ApiError(404, "station_not_supported", "Only station 22101 is supported.")
        api_key = os.environ.get("KMA_API_KEY")
        if not api_key:
            raise ApiError(503, "collection_key_missing", "The collection API key is not configured.")
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "collection_busy", "A collector is already running.")
        try:
            with ExitStack() as stack:
                # 반복 수집기와 같은 잠금 파일을 사용해 다른 프로세스와도 겹치지 않는다.
                try:
                    stack.enter_context(exclusive_collector_lock(self.settings.db_path.with_suffix(".collector.lock")))
                except RuntimeError:
                    raise ApiError(409, "collection_busy", "A collector is already running.") from None
                state_path = self.settings.db_path.with_suffix(".manual-collection.json")
                started = self.clock().astimezone(timezone.utc)
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    last = state["last_attempt_epoch"]
                    if type(last) not in (int, float) or not math.isfinite(last):
                        raise ValueError("Invalid collection cooldown state.")
                    if started.timestamp() - last < COOLDOWN_SECONDS:
                        raise ApiError(429, "collection_cooldown", "Wait 30 seconds between collection attempts.")
                # 실패한 외부 요청도 재호출 제한에 포함해 장애 시 연타를 막는다.
                _write_status(state_path, {"last_attempt_epoch": started.timestamp()})
                try:
                    report = collect_observations(
                        api_key=api_key, db_path=self.settings.db_path, station_id=station_id,
                        now=lambda tz: self.clock().astimezone(tz),
                    )
                except BuoyApiError:
                    raise ApiError(502, "collection_upstream_failed", "The upstream observation request failed.") from None
                completed = self.clock().astimezone(timezone.utc)
                # 응답을 허용 목록으로 구성해 DB 경로·키·원문 예외를 공개하지 않는다.
                return {
                    "status": "collected", "station_id": station_id,
                    "started_at_utc": started.isoformat(), "completed_at_utc": completed.isoformat(),
                    "received_count": report["received_count"], "processed_count": report["processed_count"],
                    "latest_observed_at_kst": report["latest_observed_at_kst"],
                    "message": "Existing upstream observations fetched; a new measurement is not guaranteed.",
                }
        except (OSError, sqlite3.Error):
            raise ApiError(503, "collection_storage_failed", "Collection storage is unavailable.") from None
        except (ValueError, TypeError, KeyError):
            raise ApiError(503, "collection_data_invalid", "Collection data or state is invalid.") from None
        finally:
            # 성공·실패와 관계없이 다음 수집 요청이 잠금을 얻을 수 있게 한다.
            self._lock.release()
