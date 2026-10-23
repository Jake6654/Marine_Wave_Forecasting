"""기상청 부이 API를 호출하고 응답 파서에 연결한다."""

import math
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from marine_forecast.data.buoy_api import (
    SOURCE_TIMEZONE,
    RawBuoyObservation,
    parse_buoy_response,
)

API_URL="https://apihub.kma.go.kr/api/typ01/url/kma_buoy2.php"

# 첫 버전에서는 요청량과 메모리 사용을 제한한다.
MAX_QUERY_WINDOW = timedelta(hours=24)
MAX_RESPONSE_BYTES = 1_000_000

class BuoyApiError(RuntimeError):
    """외부 API 통신 또는 응답 처리에 실패했음을 나타낸다."""


def _as_kst(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")

    # 컴퓨터의 로컬 시간대로 원본 시각을 추측하지 않는다.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone.")

    # API가 분 단위 입력을 받으므로 초를 조용히 잘라내지 않는다.
    if value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{name} must use minute precision.")

    return value.astimezone(SOURCE_TIMEZONE)

class BuoyApiClient:

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,) -> None:

        if not isinstance(api_key, str):
            raise TypeError("API key must be a string.")

        # 설정 실수를 숨기지 않고 요청 전에 발견한다.
        if not api_key or any(character.isspace() for character in api_key):
            raise ValueError("API key must be non-empty without whitespace.")

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
        ):
            raise TypeError("Timeout must be a real number.")

        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("Timeout must be finite and greater than zero.")


        self._api_key = api_key
        self._timeout_seconds = float(timeout_seconds)


    def fetch(
        self,
        *,
        station_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawBuoyObservation]:
        """기간 내 관측을 조회하고 UTC 관측 객체 목록을 반환한다."""
        if not isinstance(station_id, str):
            raise TypeError("station_id must be a string.")

        if (
            not station_id.isascii()
            or not station_id.isdigit()
            or station_id.strip("0") == ""
        ):
            raise ValueError("station_id must identify one station.")

        start_kst = _as_kst(start, name="start")
        end_kst = _as_kst(end, name="end")

        if start_kst >= end_kst:
            raise ValueError("start must be earlier than end.")

        if end_kst - start_kst > MAX_QUERY_WINDOW:
            raise ValueError("Query window must not exceed 24 hours.")

        parameters = {
            "tm1": start_kst.strftime("%Y%m%d%H%M"),
            "tm2": end_kst.strftime("%Y%m%d%H%M"),
            "stn": station_id,
            "help": "1",
            "authKey": self._api_key,
        }

        # 인증키의 특수문자도 쿼리 문자열 안에서 안전하게 인코딩한다.
        request = Request(
            f"{API_URL}?{urlencode(parameters)}",
            method="GET",
        )

        try:
            # 성공 실패 둘다 의 경우에 응답 연결을 닫아야함
            with urlopen(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                if response.status != 200:
                    raise BuoyApiError(
                        f"Unexpected API HTTP status: {response.status}."
                    )

                # 제한보다 한 바이트 읽어 초과 여부를 구분한다.
                body = response.read(MAX_RESPONSE_BYTES + 1)

        except HTTPError as error:
            status = error.code
            error.close()

            raise BuoyApiError(
                f"Buoy API returned HTTP {status}."
            ) from None

        except (URLError, TimeoutError, OSError):
            # 연결 오류의 원문이나 요청 URL을 그대로 노출하지 않는다.
            raise BuoyApiError(
                "Could not read buoy API response within the timeout."
            ) from None

        if len(body) > MAX_RESPONSE_BYTES:
            raise BuoyApiError("Buoy API response exceeded the size limit.")

        try:
            # 실제 API 응답과 공식 활용 예제에서 사용하는 인코딩이다.
            text = body.decode("euc-kr", errors="strict")
        except UnicodeDecodeError:
            raise BuoyApiError(
                "Could not decode buoy API response as EUC-KR."
            ) from None

        try:
            observations = parse_buoy_response(text)
        except ValueError:
            # HTTP 200이어도 오류 본문이나 불완전한 관측 응답일 수 있다.
            raise BuoyApiError(
                "Buoy API returned an invalid observation response."
            ) from None

        if any(
            observation.station_id != station_id
            for observation in observations
        ):
            raise BuoyApiError(
                "Buoy API returned an unexpected station."
            )

        # 조회 경계 주변의 자료가 포함되어도 요청한 범위만 반환한다.
        selected = [
            observation
            for observation in observations
            if start_kst <= observation.observed_at <= end_kst
        ]

        if not selected:
            raise BuoyApiError(
                "No observations within the requested interval."
            )

        return selected

        
        