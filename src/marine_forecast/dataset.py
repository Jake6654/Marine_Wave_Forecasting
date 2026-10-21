import json
import warnings
from pathlib import Path


import pandas as pd

from marine_forecast.features import(
    FEATURE_COLUMNS,
    HORIZON_HOURS, # 예측 간격 6
    TARGET_COLUMN,
)
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

def attach_targets(
    features: pd.DataFrame,
    observations: pd.DataFrame,
) -> pd.DataFrame:
    """입력 특성에 같은 관측소의 정확히 6시간 뒤 파고를 연결한다"""

    keys = ["station_id", "observed_at"]

    requirements = (
        ("features", features, keys + list(FEATURE_COLUMNS)),
        (
            "observations",
            observations,
            keys + ["significant_wave_height_m"],
        ),
    )
    # 두 입력 모두 연결에 필요한 열과 유일한 관측 키를 가져야 한다.
    for name, frame, required_columns in requirements:
        missing_columns = set(required_columns).difference(frame.columns)

        if missing_columns:
            raise ValueError(
                f"{name}: missing columns: {sorted(missing_columns)}"
            )

        if frame.empty:
            raise ValueError(f"{name}: no records to attach targets.")

        if frame[keys].isna().any().any():
            raise ValueError(f"{name}: observation keys must not be missing.")

        if not isinstance(frame["station_id"].dtype, pd.StringDtype):
            raise TypeError(f"{name}: station_id must use pandas string dtype.")

        if frame["station_id"].str.strip().eq("").any():
            raise ValueError(f"{name}: station_id must not be empty.")

        time_dtype = frame["observed_at"].dtype

        if not isinstance(time_dtype, pd.DatetimeTZDtype):
            raise TypeError(
                f"{name}: observed_at must be timezone-aware datetime."
            )

        if str(time_dtype.tz) != "UTC":
            raise ValueError(f"{name}: observed_at must use UTC.")

        if frame.duplicated(keys).any():
            raise ValueError(f"{name}: duplicate observation keys.")

    # 이미 정답이 붙은 표를 다시 처리하는 실수를 막는다.
    if "target_at" in features.columns or TARGET_COLUMN in features.columns:
        raise ValueError("Targets are already attached to features.")

    target_values = observations["significant_wave_height_m"]

    if not pd.api.types.is_numeric_dtype(target_values.dtype):
        raise TypeError("Target observations must have a numeric dtype.")

    if target_values.isin([float("inf"), float("-inf")]).any():
        raise ValueError("Target observations must not contain infinity.")

    # 서로 다른 시간대 가정으로 만든 두 표를 섞지 않는다.
    for attribute in ("source_timezone", "source_timezone_status"):
        if features.attrs.get(attribute) != observations.attrs.get(attribute):
            raise ValueError(
                f"Features and observations disagree on {attribute}."
            )


    result = features.loc[:, keys + list(FEATURE_COLUMNS)].copy()

  
    result["target_at"] = (
        result["observed_at"] + pd.Timedelta(hours=HORIZON_HOURS)
    )

    targets = observations.loc[
        :, keys + ["significant_wave_height_m"]
    ].copy()

    targets = targets.rename(
        columns={
            "observed_at": "target_at",
            "significant_wave_height_m": TARGET_COLUMN,
        }
    )

    result = result.merge(
        targets,
        on=["station_id", "target_at"],
        how="left",
        validate="one_to_one",
        sort=False,
    )

    output_columns = (
        keys
        + ["target_at"]
        + list(FEATURE_COLUMNS)
        + [TARGET_COLUMN]
    )

    result = (
        result.loc[:, output_columns]
        .sort_values(keys)
        .reset_index(drop=True)
    )

    # 원본 시간대 확인 상태와 예측 간격을 후속 처리에 전달한다.
    result.attrs = features.attrs.copy()
    result.attrs["horizon_hours"] = HORIZON_HOURS

    return result

def select_complete_samples(
    dataset: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """입력과 정당이 모두 있는 표본을 선택하고 제외 내역을 반환한다."""
    key_columns = ["station_id", "observed_at", "target_at"]
    feature_columns = list(FEATURE_COLUMNS)
    value_columns = feature_columns + [TARGET_COLUMN]
    required_columns = key_columns + value_columns

    missing_columns = set(required_columns).difference(dataset.columns)

    if missing_columns:
        raise ValueError(
            f"Dataset is missing columns: {sorted(missing_columns)}"
        )

    if dataset.empty:
        raise ValueError("No dataset rows to select.")

    if dataset[key_columns].isna().any().any():
        raise ValueError("Dataset keys must not be missing.")

    for column in value_columns:
        # 문자열이나 무한대를 단순 결측 표본으로 숨기지 않는다.
        if not pd.api.types.is_numeric_dtype(dataset[column].dtype):
            raise TypeError(f"{column} must have a numeric dtype.")

        if dataset[column].isin(
            [float("inf"), float("-inf")]
        ).any():
            raise ValueError(f"{column} must not contain infinity.")

    # 입력 특성 중 하나라도 비어 있는 행을 표시한다.
    # 각 셸이 결측이면 True, 값이 있으면 False인 표를 만들고
    # any(axis=1) 각 행에 하나라도 결측이면 즉 True 이면 True 을 반환
    missing_features = dataset[feature_columns].isna().any(axis=1)

    # 미래 정답이 비어 있는 행을 별도로 표시한다.
    missing_target = dataset[TARGET_COLUMN].isna()

    # 두 조건이 겹치는 행도 한 번만 제외하도록 OR로 합친다.
    excluded = missing_features | missing_target

    complete_samples = (
        # excluded Ture 인 행은 버릴 행이다
        dataset.loc[~excluded]
        .copy()
        .reset_index(drop=True)
    )

    # 제외 사유를 서로 겹치지 않는 세 그룹으로 나눈다.
    report: dict[str, object] = {
        "total_rows": len(dataset),
        "usable_rows": len(complete_samples),
        "excluded_rows": int(excluded.sum()),
        "missing_features_only": int(
            (missing_features & ~missing_target).sum()
        ),
        "missing_target_only": int(
            (~missing_features & missing_target).sum()
        ),
        "missing_both": int(
            (missing_features & missing_target).sum()
        ),
        "missing_per_feature": {
            column: int(count)
            for column, count in (
                dataset[feature_columns].isna().sum().items()
            )
        },
    }

    # 시간대 확인 상태와 예측 간격을 다음 단계에도 전달한다.
    complete_samples.attrs = dataset.attrs.copy()

    return complete_samples, report