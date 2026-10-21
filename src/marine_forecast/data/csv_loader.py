from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

REQUIRED_COLUMNS = frozenset(
    {
        "지점",
        "일시",
        "유의파고(m)",
    }
)

COLUMN_NAMES = {
    "지점": "station_id",
    "일시": "observed_at",
    "풍속(m/s)": "wind_speed_mps",
    "풍향(deg)": "wind_direction_deg",
    "현지기압(hPa)": "station_pressure_hpa",
    "습도(%)": "humidity_pct",
    "기온(°C)": "air_temperature_c",
    "수온(°C)": "water_temperature_c",
    "유의파고(m)": "significant_wave_height_m",
    "평균파고(m)": "mean_wave_height_m",
}


def read_buoy_csv(
    path: str | Path,
    *,
    encoding: str = "cp949",
) -> pd.DataFrame:
    """Read source text and validate the minimum CSV structure."""
    file_path = Path(path)

    # Preserve source strings before applying numeric and time rules.
    frame = pd.read_csv(
        file_path,
        encoding=encoding,
        encoding_errors="strict",
        dtype="string",
        keep_default_na=False,
        skip_blank_lines=False,
        on_bad_lines="error",
    )

    # 이후 코드가 열에 접근하기 전에 누락된 필수 열을 보고한다.
    missing_columns = REQUIRED_COLUMNS.difference(frame.columns)
    if missing_columns:
        raise ValueError(
            f"{file_path}: missing required columns: "
            f"{sorted(missing_columns)}"
        )

    # A header-only file cannot provide observation records
    if frame.empty:
        raise ValueError(
            f"{file_path}: CSV contains no observation records"
        )

    return frame


def _reject_invalid_values(
    raw: pd.DataFrame,
    column: str,
    invalid_mask: pd.Series,
    source_name: str,
    reason: str,
) -> None:
    # 현재 검사 중인 열에서 처음 발견한 오류를 보고한다

    # 마스크의 미확정 값을 False로 채운다.
    # 원본 데이터의 결측값을 채우는 것은 아니다
    invalid_mask = invalid_mask.fillna(False)

    # True 가 하나도 없으면 문제가 없으므로 함수 종료
    if not invalid_mask.any():
        return

    # True인 항목만 선택한 뒤, 첫 번째 항목의 인덱스를 가져온다
    index = invalid_mask[invalid_mask].index[0]

    # 행 인덱스와 열 이름으로 문제가 된 원본 셀을 읽는다.
    value = raw.at[index, column]

    # 인덱스는 0부터 시작하고 CSV 첫 레코드는 헤더이므로 2를 더한다.
    # 따옴표 안 줄바꿈이 있으면 실제 파일의 줄 번호와는 다를 수 있다.
    record_number = int(index) + 2

    # 예외를 발생시켜 처리를 중단한다.
    # !r은 따옴표와 공백을 확인하기 좋은 repr 형태로 값을 표시한다.
    raise ValueError(
        f"{source_name}: CSV record {record_number}, "
        f"column {column!r}, value {value!r}: {reason}"
    )


def normalize_buoy_data(
    frame: pd.DataFrame,
    *,
    source_timezone: str,
    source_name: str = "<dataframe>",
) -> pd.DataFrame:
    """원본 관측자료를 프로젝트 내부의 열 이름과 데이터 타입으로 변환한다."""

    # 필수 열 집합에서 실제 입력 열을 빼서 누락된 열을 찾는다.
    missing_columns = REQUIRED_COLUMNS.difference(frame.columns)

    # 필수 열이 없으면 변환을 시작하지 않고 오류를 보고한다.
    if missing_columns:
        raise ValueError(
            f"{source_name}: missing required columns: "
            f"{sorted(missing_columns)}"
        )

    # 관측 레코드가 없는 입력은 거부한다.
    if frame.empty:
        raise ValueError(
            f"{source_name}: no observation records to normalize"
        )

    # 전달받은 시간대 이름으로 시간대 객체를 만든다
    try:
        timezone = ZoneInfo(source_timezone)

    # 시간대를 찾을 수 없거나 이름이 잘못되면 출처를 포함해 보고한다.
    except (ZoneInfoNotFoundError, ValueError) as error:
        # from error는 원래 예외와 새 예외의 연결 관계를 보존한다.
        raise ValueError(
            f"{source_name}: invalid source timezone: {source_timezone!r}"
        ) from error

    # 입력을 복사하고 인덱스를 0, 1, 2…로 맞춘다.
    # 이전 인덱스는 별도 열로 보관하지 않는다.
    raw = frame.copy().reset_index(drop=True)

    # 원본 셀을 보존하기 위해 문자열 가공용 복사본을 만든다.
    text = raw.copy()

    # 모든 열을 문자열 타입으로 맞추고 앞뒤 공백을 제거한다.
    for column in text.columns:
        text[column] = text[column].astype("string").str.strip()

    for column in ("지점", "일시"):

        # 결측값이거나 공백 제거 후 빈 문자열이라면 True 로 표시한다.
        invalid_mask = text[column].isna() | text[column].eq("")

        # 문제가 있을시 원본 값과 위치를 포함한 예외를 발생
        _reject_invalid_values(
            raw,
            column,
            invalid_mask,
            source_name,
            "a non-empty value is required",
        )

    # 필수 값 검사가 모두 끝난 뒤 원본과 같은 행 인덱스의 결과 표를 만든다.
    result = pd.DataFrame(index=text.index)

    # 지점번호를 문자열로 유지하면서 내부 열 이름을 저장
    result["station_id"] = text["지점"]

    # 날짜 문자열 전체가 YYYY-MM-DD HH:MM 형태인지 검사한다.
    # 정규식의 \d는 숫자, {4}는 네 자리라는 뜻이다.
    valid_format = text["일시"].str.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"
    ).fillna(False)

    # 문자열을 날짜·시간으로 변환한다.
    # 존재하지 않는 날짜 등은 검사할 수 있도록 NaT로 표시한다.
    timestamps = pd.to_datetime(
        text["일시"],
        format="%Y-%m-%d %H:%M",
        errors="coerce",
    )

    # 형식이 틀렸거나 날짜 변환에 실패한 위치를 오류로 보고
    _reject_invalid_values(
        raw,
        "일시",
        ~valid_format | timestamps.isna(),
        source_name,
        "expected a valid timestamp in YYYY-MM-DD HH:MM format",
    )

    # 원본 시각에 전달받은 시간대를 부여한 뒤 같은 순간을 UTC로 표현한다.
    result["observed_at"] = (
        timestamps.dt.tz_localize(
            timezone,
            # 일광절약시간 변경으로 시각이 중복되면 추측하지 않고 오류를 낸다.
            ambiguous="raise",
            # 일광절약시간 변경으로 존재하지 않는 시각도 오류로 처리한다.
            nonexistent="raise",
        )
        .dt.tz_convert("UTC")
    )

    # 매핑 딕셔너리에서 원본 열 이름과 내부 열 이름을 하나씩 가져온다.
    for source_column, target_column in COLUMN_NAMES.items():

        # 지점과 일시는 이미 처리했으므로 다음 열로 넘어간다.
        if source_column in ("지점", "일시"):
            continue

        # 선택 열이 원본에 없으면 같은 행 수의 결측 열을 만든다.
        if source_column not in text.columns:
            result[target_column] = pd.Series(
                pd.NA,
                index=text.index,
                # 대문자 Float64는 결측값을 지원하는 pandas 실수 타입이다.
                dtype="Float64",
            )

            # 원본에 없는 열에 접근하지 않고 다음 열을 처리한다
            continue

        values = text[source_column]

        # 숫자 변환 전에 원래 비어 있던 위치를 기록한다.
        blank_mask = values.isna() | values.eq("")

        # 빈 값만 임시 결측으로 바꾸고 숫자로 변환한다.
        # 변환할 수 없는 문자열도 일단 결측으로 표시해 아래에서 구분한다.
        numeric = pd.to_numeric(
            values.mask(blank_mask),
            errors="coerce",
        ).astype("Float64")

        # 원래 빈 값이 아니었는데 변환 결과가 결측이면 변환 실패다.
        # 숫자로 변환된 양의 무한대와 음의 무한대도 오류에 포함한다.
        invalid_mask = (
            (~blank_mask & numeric.isna())
            | numeric.isin([float("inf"), float("-inf")])
        )

        # 변환 실패나 무한대가 있으면 해당 원본 값과 위치를 보고한다.
        _reject_invalid_values(
            raw,
            source_column,
            invalid_mask,
            source_name,
            "expected a finite number or an empty value",
        )

        # 변환된 숫자 Series를 내부 열 이름으로 결과 표에 저장한다.
        result[target_column] = numeric

    # 모든 행을 유지하고, 열 순서는 매핑 딕셔너리의 내부 이름 순서로 맞춘다.
    result = result.loc[:, list(COLUMN_NAMES.values())]

    # 결측값은 유지하면서 기본 허용 범위를 벗어나는 관측값을 표시한다.
    range_rules = {
        "풍속(m/s)": (
            result["wind_speed_mps"].lt(0),  # 음수 풍속을 오류로 표시한다.
            "expected wind speed >= 0",
        ),
        "유의파고(m)": (
            result["significant_wave_height_m"].lt(0),
            "expected significant wave height >= 0",
        ),
        "평균파고(m)": (
            result["mean_wave_height_m"].lt(0),
            "expected mean wave height >= 0",
        ),
        "습도(%)": (
            result["humidity_pct"].lt(0)
            | result["humidity_pct"].gt(100),
            "expected humidity between 0 and 100",
        ),
        "풍향(deg)": (
            result["wind_direction_deg"].lt(0)
            | result["wind_direction_deg"].gt(360),
            "expected wind direction between 0 and 360",
        ),
        "현지기압(hPa)": (
            result["station_pressure_hpa"].le(0),
            "expected station pressure > 0",
        ),
    }

    # source_column = "풍속(m/s)"
    # invalid_mask = result["wind_speed_mps"].lt(0)
    # reason = "expected wind speed >= 0" 이런식으로 튜플을 풀어서 받는다
    # 정렬 전에 검사해야 원본 레코드와 결과 행의 위치가 일치한다.
    for source_column, (invalid_mask, reason) in range_rules.items():
        _reject_invalid_values(
            raw,
            source_column,
            invalid_mask,
            source_name,
            reason,
        )

    # keep=False는 중복 그룹의 첫 번째 기록까지 모두 True로 표시한다.
    duplicate_mask = result.duplicated(
        subset=["station_id", "observed_at"],
        keep=False,
    )

    _reject_invalid_values(
        raw,
        "일시",
        duplicate_mask,
        source_name,
        "duplicate station_id and observed_at combination",
    )

    # 오류 보고가 끝난 뒤 정렬하고 반환 결과에 새로운 인덱스를 부여한다.
    result = (
        result.sort_values(["station_id", "observed_at"])
        .reset_index(drop=True)
    )

    # 매핑에 없는 원본 열 이름을 부가정보로 기록한다.
    result.attrs["unmapped_columns"] = [
        column for column in raw.columns if column not in COLUMN_NAMES
    ]

    # 호출자가 지정한 원본 시간대를 부가정보로 남긴다.
    result.attrs["source_timezone"] = source_timezone

    # 파일 경로 등 원본을 식별할 이름을 부가정보로 남긴다.
    result.attrs["source_name"] = source_name

    # 내부 열 이름과 변환된 타입을 가진 DataFrame을 반환한다.
    return result
