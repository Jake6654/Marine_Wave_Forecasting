"""기상청 부이 API 응답을 출처 정보를 보존하며 해석한다."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


SOURCE_TIMEZONE = ZoneInfo("Asia/Seoul")

# API가 반환하는 필드 순서를 명시해 위치 기반 해석을 일관되게 유지한다.
SOURCE_COLUMNS: tuple[str, ...] = (
    "TM",
    "STN",
    "WD1",
    "WS1",
    "WS1_GST",
    "WD2",
    "WS2",
    "WS2_GST",
    "PA",
    "HM",
    "TA",
    "TW",
    "WH_MAX",
    "WH_SIG",
    "WH_AVE",
    "WP",
    "WO",
    "AQC",
    "MQC",
)


@dataclass(frozen=True)
class RawBuoyObservation:
    """시각과 식별자를 해석하고 나머지 API 필드를 보존한 관측"""

    station_id: str
    observed_at: datetime
    raw_fields: tuple[str, ...]

    def get_raw(self, column: str) -> str:
        """API 필드 이름에 해당하는 원본 문자열을 반환"""
        try:
            position = SOURCE_COLUMNS.index(column)
        except ValueError:
            # 없는 필드 요청을 정상적인 결측값처럼 처리하지 않는다.
            raise KeyError(column) from None

        return self.raw_fields[position]

def parse_buoy_response(
        text: str
)-> list[RawBuoyObservation]:
    """API 검증 후 시각순으로 반환한다"""
    if not isinstance(text, str):
        raise TypeError("API response must be a string.")

    # 앞뒤 공백 제거후 줄바꿈 기준으로 list 을 만들어 반환
    lines = text.strip().splitlines()

    # HTTP 200이어도 JSON 오류나 불완전한 본문일 수 있다.
    if (
        len(lines) < 2
        or lines[0].strip() != "#START7777"
        or lines[-1].strip() != "#7777END"
    ):
        raise ValueError(
            "API response has missing or invalid boundary markers."
        )

    observations: list[RawBuoyObservation] = []
    # 중복처리
    # ("22101", 관측시각) 형태의 튜플을 담는 집합
    # seen = {
    # (
    #     "22101",
    #     datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc),
    # )
    # }
    seen: set[tuple[str, datetime]] = set()

    # 시작과 종료 표식을 제외하고 한줄씩 처리
    for line_number, line in enumerate(lines[1:-1], start=2):
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue


        try:
            fields = next(
                csv.reader(
                    [stripped],
                    skipinitialspace=True,
                    strict=True,
                )
            )
        except csv.Error:
            # 응답 원문을 오류 메시지에 포함하지 않는다.
            raise ValueError(
                f"Invalid CSV syntax at response line {line_number}."
            ) from None

        fields = [field.strip() for field in fields]

        # 마지막 '='까지 검사해 형식 변경이나 잘린 행을 탐지한다.
        if (
            len(fields) != len(SOURCE_COLUMNS) + 1
            or fields[-1] != "="
        ):
            raise ValueError(
                f"Unexpected API fields at response line {line_number}."
            )

        # 19개의 문자열
        raw_fields = tuple(fields[:-1])
        raw_time = raw_fields[0]
        station_id = raw_fields[1]

        if not station_id.isascii() or not station_id.isdigit():
            raise ValueError(
                f"Invalid station ID at response line {line_number}."
            )

        if (
            len(raw_time) != 12
            or not raw_time.isascii()
            or not raw_time.isdigit()
        ):
            raise ValueError(
                f"Invalid timestamp at response line {line_number}."
            )

        try:
            local_time = datetime.strptime(
                raw_time,
                "%Y%m%d%H%M",
            )
        except ValueError:
            raise ValueError(
                f"Invalid calendar timestamp at response line {line_number}."
            ) from None

        # 원본 숫자는 KST 시각이므로 먼저 시간대를 부여한 뒤 UTC로 변환한다.
        observed_at = local_time.replace(
            tzinfo=SOURCE_TIMEZONE,
        ).astimezone(timezone.utc)

        key = (station_id, observed_at)

        # 같은 관측을 임의로 선택하면 수신 순서에 따라 결과가 달라질 수 있다.
        if key in seen:
            raise ValueError(
                f"Duplicate observation at response line {line_number}."
            )

        seen.add(key)

        observations.append(
            RawBuoyObservation(
                station_id=station_id,
                observed_at=observed_at,
                raw_fields=raw_fields,
            )
        )

    if not observations:
        raise ValueError("API response contains no observations.")

    # 실제 API 응답 순서가 시간순이라는 가정을 하지 않는다.
    observations.sort(
        key=lambda observation: (
            observation.station_id,
            observation.observed_at,
        )
    )

    return observations
