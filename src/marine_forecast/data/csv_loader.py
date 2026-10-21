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
    """첫 번째 오류의 원본 값과 CSV 레코드 위치를 보고한다."""

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

    # 필수 값 검사가 모두 끝난 뒤 결과 표를 한 번만 만든다.
    result = pd.DataFrame(index=text.index)

    # 지점번호를 문자열로 유지하면서 내부 열 이름을 저장한다.
    result["station_id"] = text["지점"]

    # 문자열 형식과 실제 달력 값은 별도로 검사한다.
    valid_format = text["일시"].str.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"
    ).fillna(False)

    # 변환 실패를 NaT로 표시한 뒤 원본 값과 함께 오류로 보고한다.
    timestamps = pd.to_datetime(
        text["일시"],
        format="%Y-%m-%d %H:%M",
        errors="coerce",
    )

    _reject_invalid_values(
        raw,
        "일시",
        ~valid_format | timestamps.isna(),
        source_name,
        "expected a valid timestamp in YYYY-MM-DD HH:MM format",
    )

    # 원본 시각에 명시된 시간대를 부여하고 같은 순간을 UTC로 변환한다.
    result["observed_at"] = (
        timestamps.dt.tz_localize(
            timezone,
            ambiguous="raise",
            nonexistent="raise",
        )
        .dt.tz_convert("UTC")
    )

    for source_column, target_column in COLUMN_NAMES.items():
        # 지점과 관측시각은 앞에서 처리했다.
        if source_column in ("지점", "일시"):
            continue

        if source_column not in text.columns:
            # 선택 열이 없어도 결과의 열 구조와 실수 타입을 유지한다.
            result[target_column] = pd.Series(
                pd.NA,
                index=text.index,
                dtype="Float64",
            )
            continue

        values = text[source_column]
        blank_mask = values.isna() | values.eq("")

        # 빈 값만 결측으로 허용하고 비어 있지 않은 변환 실패는 뒤에서 거부한다.
        numeric = pd.to_numeric(
            values.mask(blank_mask),
            errors="coerce",
        ).astype("Float64")

        invalid_mask = (
            (~blank_mask & numeric.isna())
            | numeric.isin([float("inf"), float("-inf")])
        )
        _reject_invalid_values(
            raw,
            source_column,
            invalid_mask,
            source_name,
            "expected a finite number or an empty value",
        )
        result[target_column] = numeric

    # 출력 열 순서를 고정하고, 매핑하지 않은 원본 열을 부가정보로 남긴다.
    result = result.loc[:, list(COLUMN_NAMES.values())]
    result.attrs["unmapped_columns"] = [
        column for column in raw.columns if column not in COLUMN_NAMES
    ]
    result.attrs["source_timezone"] = source_timezone
    result.attrs["source_name"] = source_name

    return result
