"""앱 팩토리·수명주기·HTTP 경로·일관된 오류 응답을 연결한다."""

import logging
import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from marine_forecast.api.schemas import (
    CollectionResponse, ErrorDetail, ErrorResponse, HealthResponse, ObservationResponse, PredictionResponse, ValidationIssue,
)
from marine_forecast.api.collection import ManualCollection
from marine_forecast.api.background_collection import BackgroundCollection
from marine_forecast.api.service import ApiError, ForecastService, utc_now
from marine_forecast.api.settings import ApiSettings
from marine_forecast.model_artifact import load_model_artifact


logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str, details=None, headers=None):
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"),
                        headers={"Cache-Control": "no-store", **(headers or {})})


def create_app(settings: ApiSettings | None = None, *, clock: Callable[[], datetime] = utc_now) -> FastAPI:
    config = settings if settings is not None else ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bundle = None
        if config.trust_local_model:
            try:
                # 서버 시작 시 한 번만 신뢰한 모델을 읽는다.
                bundle = load_model_artifact(config.model_path, trusted=True)
            except Exception as error:
                # 실패해도 관측 조회와 liveness는 제공하되 경로·원문 예외는 공개하지 않는다.
                logger.warning("Model loading failed (%s).", type(error).__name__)
        app.state.forecast_service = ForecastService(config, bundle, clock=clock)
        app.state.manual_collection = ManualCollection(config, clock=clock)
        collector = BackgroundCollection(config) if config.background_collection else None
        try:
            if collector is not None:
                collector.start()
            yield
        finally:
            if collector is not None:
                # 동기 네트워크 작업의 종료를 기다릴 때 이벤트 루프를 막지 않는다.
                await asyncio.to_thread(collector.stop)
            # 요청마다 만든 DB 연결은 저장소가 닫는다. 모델 참조는 종료 시 해제한다.
            del app.state.forecast_service
            del app.state.manual_collection

    def reject_query_parameters(request: Request):
        if request.query_params:
            raise ApiError(422, "invalid_request", "This API does not accept query parameters.")

    app = FastAPI(
        title="Marine Wave Forecast API", version="0.1.0", lifespan=lifespan,
        description="Station 22101 observations and guarded experimental six-hour forecasts. Not for navigation safety.",
        dependencies=[Depends(reject_query_parameters)],
    )

    def service(request: Request) -> ForecastService:
        return request.app.state.forecast_service

    @app.middleware("http")
    async def disable_response_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        return _error(error.status_code, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # 입력값·요청 본문·예외 원문 대신 위치와 오류 종류만 노출한다.
        details = [ValidationIssue(location=list(item["loc"]), type=item["type"]) for item in error.errors()]
        return _error(422, "invalid_request", "Request validation failed.", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        code = "not_found" if error.status_code == 404 else "method_not_allowed" if error.status_code == 405 else "http_error"
        return _error(error.status_code, code, "The requested HTTP operation is unavailable.", headers=error.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logger.error("Internal request failure (%s).", type(error).__name__)
        return _error(500, "internal_error", "An internal error occurred.")

    error_responses = {code: {"model": ErrorResponse} for code in (404, 422, 503, 500)}

    @app.get("/health/live", response_model=HealthResponse)
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", response_model=HealthResponse, responses=error_responses)
    def ready(svc: ForecastService = Depends(service)):
        return svc.ready()

    @app.get("/v1/stations/{station_id}/observations/latest", response_model=ObservationResponse, responses=error_responses)
    def latest_observation(
        station_id: str = Path(pattern=r"^[0-9]{1,8}$"), svc: ForecastService = Depends(service),
    ):
        return svc.latest_observation(station_id)

    @app.get("/v1/stations/{station_id}/predictions/latest", response_model=PredictionResponse,
             responses=error_responses)
    def latest_prediction(
        station_id: str = Path(pattern=r"^[0-9]{1,8}$"), svc: ForecastService = Depends(service),
    ):
        return svc.latest_prediction(station_id)

    @app.post("/v1/stations/{station_id}/observations/collect", response_model=CollectionResponse,
              responses={code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 429, 502, 503)})
    def collect_observations_now(request: Request, station_id: str = Path(pattern=r"^[0-9]{1,8}$")):
        return request.app.state.manual_collection.collect(
            station_id, request.headers.get("x-collection-token", ""),
        )

    return app
