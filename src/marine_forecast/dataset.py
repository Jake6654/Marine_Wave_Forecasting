import json
import warnings
from pathlib import Path

import pandas as pd

from marine_forecast.data.csv_loader import COLUMN_NAMES


def load_processed_data(
    path: str | Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """정제 CSV와 처리 기록을 읽고 내부 자료형을 복원한다."""
    csv_path = Path(path).resolve()
    metadata_path = csv_path.with_suffix(".metadata.json")

    with metadata_path.open("r", encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)

    # JSON 배열이나 단일 값은 처리 기록으로 사용할 수 없다.
    if not isinstance(metadata, dict):
        raise ValueError(f"{metadata_path}: expected a JSON object")

    expected_columns = list(COLUMN_NAMES.values())

    if metadata.get("columns") != expected_columns:
        raise ValueError(
            f"{metadata_path}: unexpected observation columns"
        )

    if metadata.get("output_timezone") != "UTC":
        raise ValueError(
            f"{metadata_path}: expected output_timezone='UTC'"
        )

    timezone_status = metadata.get("source_timezone_status")
    if not isinstance(timezone_status, str) or not timezone_status:
        raise ValueError(
            f"{metadata_path}: missing source_timezone_status"
        )

    numeric_columns = [
        column
        for column in expected_columns
        if column not in ("station_id", "observed_at")
    ]

    # 식별자의 앞자리 0을 보존하고, 기상 수치는 결측 지원 실수로 복원한다.
    dtype_map = {
        "station_id": "string",
        "observed_at": "string",
        **{column: "Float64" for column in numeric_columns},
    }

    frame = pd.read_csv(
        csv_path,
        encoding="utf-8",
        encoding_errors="strict",
        dtype=dtype_map,
        keep_default_na=False,
        na_values=[""],
        skip_blank_lines=False,
        on_bad_lines="error",
    )

    if frame.columns.tolist() != expected_columns:
        raise ValueError(
            f"{csv_path}: columns do not match metadata"
        )

    # bool도 int처럼 비교되므로 행 수는 정확히 int 타입인지 확인한다.
    if (
        type(metadata.get("rows")) is not int
        or frame.empty
        or len(frame) != metadata["rows"]
    ):
        raise ValueError(
            f"{csv_path}: observation count does not match metadata"
        )

    station_missing = (
        frame["station_id"].isna()
        | frame["station_id"].str.strip().eq("")
    )
    if station_missing.any():
        raise ValueError(f"{csv_path}: station_id must not be empty")

    # 시간대 없는 시각에 utc=True만 적용하면 UTC라고 잘못 가정할 수 있다.
    # 먼저 B5가 저장한 UTC 오프셋 표기가 있는지 검사한다.
    has_utc_offset = (
        frame["observed_at"]
        .str.endswith(("+00:00", "Z"))
        .fillna(False)
    )
    if not has_utc_offset.all():
        raise ValueError(
            f"{csv_path}: observed_at must include a UTC offset"
        )

    # 이미 저장된 시간대 정보를 해석하며 원본 시간대를 새로 추정하지 않는다.
    frame["observed_at"] = pd.to_datetime(
        frame["observed_at"],
        format="ISO8601",
        errors="raise",
        utc=True,
    )

    if frame[numeric_columns].isin(
        [float("inf"), float("-inf")]
    ).any().any():
        raise ValueError(f"{csv_path}: infinite values are not allowed")

    if frame.duplicated(["station_id", "observed_at"]).any():
        raise ValueError(f"{csv_path}: duplicate observation keys")

    # 저장된 정렬 상태가 바뀌었으면 조용히 고치지 않고 알려준다.
    keys = pd.MultiIndex.from_frame(
        frame[["station_id", "observed_at"]]
    )
    if not keys.is_monotonic_increasing:
        raise ValueError(
            f"{csv_path}: observations must be sorted by station and time"
        )

    actual_missing = {
        column: int(count)
        for column, count in frame.isna().sum().items()
    }
    if actual_missing != metadata.get("missing_counts"):
        raise ValueError(
            f"{csv_path}: missing counts do not match metadata"
        )

    # 후속 특성 생성에서도 원본 시간대의 확인 상태를 추적할 수 있게 한다.
    frame.attrs["source_timezone_status"] = timezone_status
    frame.attrs["source_timezone"] = metadata.get("source_timezone")

    if timezone_status != "confirmed":
        warnings.warn(
            "Source timezone is not confirmed; "
            "timestamps retain the preparation-time assumption.",
            UserWarning,
            stacklevel=2,
        )

    return frame, metadata