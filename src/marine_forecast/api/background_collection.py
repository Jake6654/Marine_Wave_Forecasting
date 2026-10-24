"""단일 API 프로세스에서 시작 시 관측 복구와 주기적 갱신을 담당한다."""

import logging
import os
import threading

from marine_forecast.api.settings import ApiSettings
from marine_forecast.collector_runtime import exclusive_collector_lock
from marine_forecast.data.buoy_collection import collect_observations


logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 600


class BackgroundCollection:
    """무료 서버가 실행 중일 때만 동작하는 수집 작업이다."""

    def __init__(self, settings: ApiSettings):
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Background collector has already been started.")
        if not os.environ.get("KMA_API_KEY"):
            raise ValueError("KMA_API_KEY is required for background collection.")
        # 외부 API를 기다리느라 FastAPI의 HTTP 시작을 지연시키지 않는다.
        self._thread = threading.Thread(
            target=self._run, name="buoy-background-collection", daemon=True,
        )
        self._thread.start()

    def collect_once(self) -> bool:
        try:
            # 수집하는 동안에만 잠가 수동 수집 요청과도 공존한다.
            with exclusive_collector_lock(self.settings.db_path.with_suffix(".collector.lock")):
                report = collect_observations(
                    api_key=os.environ["KMA_API_KEY"], db_path=self.settings.db_path,
                    station_id="22101", lookback_hours=12,
                )
                logger.info("Background observations collected: %s.", report["received_count"])
                return True
        except Exception as error:
            # 키·URL·원문 예외는 로그에 남기지 않고 다음 주기에 재시도한다.
            # 실패해도 기존 DB를 지우거나 과거 예측을 최신으로 가장하지 않는다.
            logger.warning("Background collection failed (%s); retry after interval.", type(error).__name__)
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            self.collect_once()
            # 완료 시점부터 기다리고, 종료 요청이 오면 대기를 즉시 끝낸다.
            if self._stop.wait(INTERVAL_SECONDS):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # 요청 도중 종료되면 진행 중인 제한 시간 있는 API 호출을 잠시 기다린다.
            self._thread.join(timeout=20)
            if self._thread.is_alive():
                logger.warning("Background collector did not stop within shutdown grace period.")
