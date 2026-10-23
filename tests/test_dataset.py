import hashlib
import json
import warnings
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from marine_forecast.data.csv_loader import COLUMN_NAMES
from marine_forecast.dataset import (
    attach_targets,
    load_processed_data,
    select_complete_samples,
    split_by_time,
)
from marine_forecast.features import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    build_features,
)


TIMEZONE_ATTRS = {
    "source_timezone": "UTC",
    "source_timezone_status": "caller_supplied_unverified",
}
BOUNDARIES = {
    "validation_start": "2025-01-02T00:00:00Z",
    "test_start": "2025-01-03T00:00:00Z",
    "test_end": "2025-01-04T00:00:00Z",
}


def make_observations(periods: int = 12) -> pd.DataFrame:
    """실제 구현에 의존하지 않고 기대값을 계산할 관측 표를 만든다."""
    frame = pd.DataFrame(
        {
            "station_id": pd.Series(["00123"] * periods, dtype="string"),
            "observed_at": pd.date_range(
                "2025-01-01", periods=periods, freq="h", tz="UTC"
            ),
        }
    )
    for column in list(COLUMN_NAMES.values())[2:]:
        frame[column] = pd.Series([pd.NA] * periods, dtype="Float64")
    frame["significant_wave_height_m"] = pd.Series(range(periods), dtype="Float64")
    frame["wind_speed_mps"] = pd.Series([0.0] * periods, dtype="Float64")
    frame["station_pressure_hpa"] = pd.Series([1000.0] * periods, dtype="Float64")
    frame = frame.loc[:, list(COLUMN_NAMES.values())]
    frame.attrs = TIMEZONE_ATTRS.copy()
    return frame


def make_feature_table(observations: pd.DataFrame) -> pd.DataFrame:
    """정답 연결 단위 테스트에서 특성 생성 함수를 독립시킨다."""
    frame = observations[["station_id", "observed_at"]].copy()
    for column in FEATURE_COLUMNS:
        if column in observations:
            frame[column] = observations[column]
        else:
            frame[column] = pd.Series(1.0, index=frame.index, dtype="Float64")
    frame.attrs = observations.attrs.copy()
    return frame


def make_samples(times: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "station_id": pd.Series(["00123"] * len(times), dtype="string"),
            "observed_at": times,
            "target_at": times + pd.Timedelta(hours=6),
        }
    )
    for column in FEATURE_COLUMNS:
        frame[column] = pd.Series([1.0] * len(times), dtype="Float64")
    frame[TARGET_COLUMN] = pd.Series([2.0] * len(times), dtype="Float64")
    frame.attrs = {**TIMEZONE_ATTRS, "horizon_hours": 6}
    return frame


@pytest.fixture
def observations() -> pd.DataFrame:
    return make_observations()


@pytest.fixture
def samples() -> pd.DataFrame:
    # 불규칙한 간격에서도 행 개수가 아니라 실제 시각으로 경계를 판단한다.
    times = [
        f"2025-01-{day:02d}T{hour:02d}:00:00Z"
        for day in (1, 2, 3)
        for hour in (0, 17, 18, 23)
    ] + ["2025-01-04T00:00:00Z"]
    return make_samples(pd.DatetimeIndex(times))


def write_metadata(path: Path, metadata: object) -> None:
    path.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_processed_pair(
    tmp_path: Path, observations: pd.DataFrame
) -> tuple[Path, dict[str, object]]:
    # 저장 테스트는 임시 폴더만 사용해 프로젝트의 데이터 원본을 보존한다.
    path = tmp_path / "observations.csv"
    observations.to_csv(path, index=False, encoding="utf-8")
    metadata = {
        "columns": list(COLUMN_NAMES.values()),
        "rows": len(observations),
        "output_timezone": "UTC",
        **TIMEZONE_ATTRS,
        "missing_counts": {
            name: int(count) for name, count in observations.isna().sum().items()
        },
    }
    write_metadata(path, metadata)
    return path, metadata


def test_load_restores_types_values_metadata_and_preserves_files(
    tmp_path: Path, observations: pd.DataFrame
) -> None:
    observations.loc[0, "air_temperature_c"] = -3.5
    path, expected_metadata = write_processed_pair(tmp_path, observations)
    before = (path.read_bytes(), path.with_suffix(".metadata.json").read_bytes())

    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        frame, metadata = load_processed_data(path)

    assert metadata == expected_metadata
    assert frame["station_id"].tolist() == ["00123"] * 12
    assert isinstance(frame["station_id"].dtype, pd.StringDtype)
    assert isinstance(frame["observed_at"].dtype, pd.DatetimeTZDtype)
    assert str(frame["observed_at"].dt.tz) == "UTC"
    for column in list(COLUMN_NAMES.values())[2:]:
        assert str(frame[column].dtype) == "Float64"
    assert frame.loc[0, "significant_wave_height_m"] == 0
    assert frame.loc[0, "air_temperature_c"] == -3.5
    assert pd.isna(frame.loc[1, "air_temperature_c"])
    assert frame.attrs == TIMEZONE_ATTRS
    assert before == (path.read_bytes(), path.with_suffix(".metadata.json").read_bytes())
    assert_frame_equal(frame, observations)


def test_confirmed_synthetic_metadata_does_not_warn(
    tmp_path: Path, observations: pd.DataFrame
) -> None:
    path, metadata = write_processed_pair(tmp_path, observations)
    # 이 상태는 가상 데이터에만 적용하며 실제 CSV의 시간대는 확정하지 않는다.
    metadata["source_timezone_status"] = "confirmed"
    write_metadata(path, metadata)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frame, _ = load_processed_data(path)
    assert not caught
    assert frame.attrs["source_timezone_status"] == "confirmed"


@pytest.mark.parametrize("metadata", [[], None, 1, "invalid"])
def test_load_rejects_non_object_metadata(tmp_path: Path, metadata: object) -> None:
    path = tmp_path / "observations.csv"
    write_metadata(path, metadata)
    with pytest.raises(ValueError, match="expected a JSON object"):
        load_processed_data(path)


def test_load_rejects_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "observations.csv"
    path.with_suffix(".metadata.json").write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_processed_data(path)


@pytest.mark.parametrize("missing", ["csv", "metadata"])
def test_load_reports_missing_files(
    tmp_path: Path, observations: pd.DataFrame, missing: str
) -> None:
    path, _ = write_processed_pair(tmp_path, observations)
    target = path if missing == "csv" else path.with_suffix(".metadata.json")
    # 이 파일은 이 테스트가 만든 임시 파일이므로 삭제해 누락 상황을 재현한다.
    target.unlink()
    with pytest.raises(FileNotFoundError):
        load_processed_data(path)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("columns", [], "unexpected observation columns"),
        ("output_timezone", "Asia/Seoul", "output_timezone"),
        ("source_timezone_status", None, "source_timezone_status"),
        ("source_timezone_status", "", "source_timezone_status"),
        ("rows", 13, "observation count"),
        ("rows", True, "observation count"),
        ("rows", "12", "observation count"),
        ("missing_counts", {}, "missing counts"),
    ],
)
def test_load_rejects_inconsistent_metadata(
    tmp_path: Path, observations: pd.DataFrame, field: str, value: object, message: str
) -> None:
    path, metadata = write_processed_pair(tmp_path, observations)
    metadata[field] = value
    write_metadata(path, metadata)
    with pytest.raises(ValueError, match=message):
        load_processed_data(path)


def test_load_checks_actual_csv_columns(tmp_path: Path, observations: pd.DataFrame) -> None:
    path, _ = write_processed_pair(tmp_path, observations)
    observations.rename(columns={"humidity_pct": "unexpected"}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="columns do not match metadata"):
        load_processed_data(path)


@pytest.mark.parametrize(
    "kind,message",
    [
        ("empty", "observation count"),
        ("missing_station", "station_id must not be empty"),
        ("blank_station", "station_id must not be empty"),
        ("naive_time", "UTC offset"),
        ("missing_time", "UTC offset"),
        ("invalid_time", None),
        ("infinity", "infinite values"),
        ("duplicate", "duplicate observation keys"),
        ("unsorted", "must be sorted"),
    ],
)
def test_load_rejects_invalid_observations(
    tmp_path: Path, observations: pd.DataFrame, kind: str, message: str | None
) -> None:
    if kind == "empty":
        observations = observations.iloc[:0]
    elif kind == "missing_station":
        observations.loc[0, "station_id"] = pd.NA
    elif kind == "blank_station":
        observations.loc[0, "station_id"] = " "
    elif kind in ("naive_time", "missing_time", "invalid_time"):
        observations["observed_at"] = observations["observed_at"].astype("string")
        observations.loc[0, "observed_at"] = {
            "naive_time": "2025-01-01T00:00:00",
            "missing_time": pd.NA,
            "invalid_time": "not-a-dateZ",
        }[kind]
    elif kind == "infinity":
        observations.loc[0, "wind_speed_mps"] = float("inf")
    elif kind == "duplicate":
        observations = pd.concat([observations, observations.iloc[[0]]], ignore_index=True)
    else:
        observations = observations.iloc[::-1]
    path, _ = write_processed_pair(tmp_path, observations)
    with pytest.raises(ValueError, match=message):
        load_processed_data(path)


def test_attach_matches_six_hours_and_preserves_inputs(observations: pd.DataFrame) -> None:
    features = make_feature_table(observations)
    original_features = features.copy(deep=True)
    original_observations = observations.copy(deep=True)

    result = attach_targets(features, observations)

    expected = pd.Series(list(range(6, 12)) + [pd.NA] * 6, dtype="Float64")
    assert_series_equal(result[TARGET_COLUMN], expected, check_names=False)
    assert_series_equal(
        result["target_at"], result["observed_at"] + pd.Timedelta(hours=6),
        check_names=False,
    )
    assert result.columns.tolist() == (
        ["station_id", "observed_at", "target_at"] + list(FEATURE_COLUMNS) + [TARGET_COLUMN]
    )
    assert result.attrs == {**TIMEZONE_ATTRS, "horizon_hours": 6}
    assert_frame_equal(features, original_features)
    assert_frame_equal(observations, original_observations)


@pytest.mark.parametrize("kind", ["missing_hour", "missing_value"])
def test_attach_does_not_substitute_another_future_observation(
    observations: pd.DataFrame, kind: str
) -> None:
    features = make_feature_table(observations)
    if kind == "missing_hour":
        observations = observations.drop(index=6)
    else:
        observations.loc[6, "significant_wave_height_m"] = pd.NA
    result = attach_targets(features, observations)
    assert pd.isna(result.loc[0, TARGET_COLUMN])
    assert result.loc[1, TARGET_COLUMN] == 7
    assert len(result) == len(features)


def test_attach_never_borrows_another_stations_target(observations: pd.DataFrame) -> None:
    features = make_feature_table(observations)
    other = observations.copy()
    other["station_id"] = pd.Series(["00456"] * len(other), dtype="string")
    other["significant_wave_height_m"] += 100
    combined = pd.concat([observations.drop(index=6), other], ignore_index=True)
    combined.attrs = TIMEZONE_ATTRS.copy()
    result = attach_targets(features, combined)
    assert pd.isna(result.loc[0, TARGET_COLUMN])
    assert result.loc[1, TARGET_COLUMN] == 7


def test_attach_targets_across_midnight(observations: pd.DataFrame) -> None:
    observations["observed_at"] += pd.Timedelta(hours=22)
    result = attach_targets(make_feature_table(observations), observations)
    assert result.loc[0, "target_at"] == pd.Timestamp("2025-01-02T04:00:00Z")
    assert result.loc[0, TARGET_COLUMN] == 6


@pytest.mark.parametrize("column", ["target_at", TARGET_COLUMN])
def test_attach_rejects_existing_targets(observations: pd.DataFrame, column: str) -> None:
    features = make_feature_table(observations)
    features[column] = pd.NA
    with pytest.raises(ValueError, match="already attached"):
        attach_targets(features, observations)


@pytest.mark.parametrize("side", ["features", "observations"])
@pytest.mark.parametrize(
    "kind,message",
    [
        ("missing_column", "missing columns"),
        ("empty", "no records"),
        ("missing_key", "keys must not be missing"),
        ("blank_station", "station_id must not be empty"),
        ("numeric_station", "string dtype"),
        ("string_time", "timezone-aware"),
        ("naive_time", "timezone-aware"),
        ("other_timezone", "must use UTC"),
        ("duplicate", "duplicate observation keys"),
    ],
)
def test_attach_validates_both_inputs(
    observations: pd.DataFrame, side: str, kind: str, message: str
) -> None:
    inputs = {"features": make_feature_table(observations), "observations": observations.copy()}
    frame = inputs[side]
    if kind == "missing_column":
        frame = frame.drop(columns="observed_at")
    elif kind == "empty":
        frame = frame.iloc[:0]
    elif kind == "missing_key":
        frame.loc[0, "observed_at"] = pd.NaT
    elif kind == "blank_station":
        frame.loc[0, "station_id"] = " "
    elif kind == "numeric_station":
        frame["station_id"] = 123
    elif kind == "string_time":
        frame["observed_at"] = frame["observed_at"].astype("string")
    elif kind == "naive_time":
        frame["observed_at"] = frame["observed_at"].dt.tz_localize(None)
    elif kind == "other_timezone":
        frame["observed_at"] = frame["observed_at"].dt.tz_convert("Asia/Seoul")
    else:
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    inputs[side] = frame
    with pytest.raises((TypeError, ValueError), match=message):
        attach_targets(**inputs)


@pytest.mark.parametrize("attribute", ["source_timezone", "source_timezone_status"])
def test_attach_rejects_mismatched_assumptions(
    observations: pd.DataFrame, attribute: str
) -> None:
    features = make_feature_table(observations)
    features.attrs[attribute] = "different"
    with pytest.raises(ValueError, match=attribute):
        attach_targets(features, observations)


@pytest.mark.parametrize("kind", ["string", "positive_infinity", "negative_infinity"])
def test_attach_rejects_invalid_target_values(observations: pd.DataFrame, kind: str) -> None:
    features = make_feature_table(observations)
    if kind == "string":
        observations["significant_wave_height_m"] = observations["significant_wave_height_m"].astype("string")
    else:
        observations.loc[6, "significant_wave_height_m"] = (
            float("inf") if kind == "positive_infinity" else float("-inf")
        )
    with pytest.raises((TypeError, ValueError), match="numeric dtype|infinity"):
        attach_targets(features, observations)


def test_select_reports_disjoint_missing_reasons_and_preserves_zero() -> None:
    dataset = make_samples(pd.date_range("2025-01-01", periods=8, freq="h", tz="UTC"))
    dataset["unused_air_temperature"] = pd.NA
    dataset.loc[0, "significant_wave_height_m"] = 0
    dataset.loc[0, "wave_height_change_1h_m"] = -1
    dataset.loc[1, "wave_height_lag_1h_m"] = pd.NA
    dataset.loc[2, TARGET_COLUMN] = pd.NA
    dataset.loc[3, ["wave_height_lag_3h_m", TARGET_COLUMN]] = pd.NA
    dataset.loc[4, ["wave_height_lag_1h_m", "wave_height_lag_6h_m"]] = pd.NA
    dataset.index = range(100, 108)
    original = dataset.copy(deep=True)

    complete, report = select_complete_samples(dataset)

    assert report == {
        "total_rows": 8, "usable_rows": 4, "excluded_rows": 4,
        "missing_features_only": 2, "missing_target_only": 1, "missing_both": 1,
        "missing_per_feature": {
            column: {"wave_height_lag_1h_m": 2, "wave_height_lag_3h_m": 1,
                     "wave_height_lag_6h_m": 1}.get(column, 0)
            for column in FEATURE_COLUMNS
        },
    }
    assert_frame_equal(complete, dataset.iloc[[0, 5, 6, 7]].reset_index(drop=True))
    assert complete.loc[0, "significant_wave_height_m"] == 0
    assert complete.loc[0, "wave_height_change_1h_m"] == -1
    assert complete.attrs == original.attrs
    assert_frame_equal(dataset, original)


@pytest.mark.parametrize("all_missing", [False, True])
def test_select_handles_all_valid_or_all_excluded(samples: pd.DataFrame, all_missing: bool) -> None:
    if all_missing:
        samples[TARGET_COLUMN] = pd.Series(pd.NA, index=samples.index, dtype="Float64")
    complete, report = select_complete_samples(samples)
    assert len(complete) == (0 if all_missing else 13)
    assert report["excluded_rows"] == (13 if all_missing else 0)
    assert complete.attrs == samples.attrs


@pytest.mark.parametrize("column", ["station_id", "observed_at", "target_at"])
def test_select_rejects_missing_keys(samples: pd.DataFrame, column: str) -> None:
    samples.loc[0, column] = pd.NA
    with pytest.raises(ValueError, match="keys must not be missing"):
        select_complete_samples(samples)


@pytest.mark.parametrize("column", [*FEATURE_COLUMNS, TARGET_COLUMN])
@pytest.mark.parametrize("kind", ["string", "infinity"])
def test_select_rejects_invalid_numeric_values(samples: pd.DataFrame, column: str, kind: str) -> None:
    if kind == "string":
        samples[column] = samples[column].astype("string")
    else:
        samples.loc[0, column] = float("inf")
    with pytest.raises((TypeError, ValueError), match="numeric dtype|infinity"):
        select_complete_samples(samples)


def test_split_purges_exact_boundaries_and_reports_counts(samples: pd.DataFrame) -> None:
    original = samples.copy(deep=True)
    splits, report = split_by_time(samples, **BOUNDARIES)

    assert report == {
        "input_rows": 13, "used_rows": 6, "excluded_rows": 7,
        "split_rows": {"train": 2, "validation": 2, "test": 2},
        "purged_boundary_rows": {"train": 2, "validation": 2, "test": 2},
        "outside_period_rows": 1,
        "boundaries": {name: pd.Timestamp(value).isoformat() for name, value in BOUNDARIES.items()},
        "source_timezone_status": "caller_supplied_unverified",
    }
    for name, day in (("train", 1), ("validation", 2), ("test", 3)):
        expected = samples.loc[samples["observed_at"].dt.day.eq(day)].iloc[:2].reset_index(drop=True)
        assert_frame_equal(splits[name], expected)
        assert splits[name].attrs == {
            **samples.attrs, "split": name,
            "split_end": pd.Timestamp(list(BOUNDARIES.values())[day - 1]).isoformat(),
        }
        assert splits[name]["target_at"].lt(pd.Timestamp(splits[name].attrs["split_end"])).all()
    assert_frame_equal(samples, original)


def test_split_sorts_without_changing_input_and_never_overlaps(samples: pd.DataFrame) -> None:
    shuffled = samples.sample(frac=1, random_state=42)
    original = shuffled.copy(deep=True)
    splits, _ = split_by_time(shuffled, **BOUNDARIES)
    keys = []
    for frame in splits.values():
        assert_frame_equal(frame, frame.sort_values(["observed_at", "station_id"]).reset_index(drop=True))
        keys.append(set(zip(frame["station_id"], frame["observed_at"])))
    assert not (keys[0] & keys[1] or keys[0] & keys[2] or keys[1] & keys[2])
    assert_frame_equal(shuffled, original)


def test_split_converts_explicit_offset_boundaries_to_utc(samples: pd.DataFrame) -> None:
    offset_boundaries = {
        name: pd.Timestamp(value).tz_convert("Asia/Seoul") for name, value in BOUNDARIES.items()
    }
    expected, expected_report = split_by_time(samples, **BOUNDARIES)
    result, report = split_by_time(samples, **offset_boundaries)
    assert report == expected_report
    for name in result:
        assert_frame_equal(result[name], expected[name])


@pytest.mark.parametrize("name", list(BOUNDARIES))
@pytest.mark.parametrize("value", ["2025-01-02", "not-a-date", pd.NaT])
def test_split_rejects_ambiguous_or_invalid_boundaries(samples: pd.DataFrame, name: str, value: object) -> None:
    boundaries = {**BOUNDARIES, name: value}
    with pytest.raises(ValueError, match=name):
        split_by_time(samples, **boundaries)


@pytest.mark.parametrize(
    "changes",
    [
        {"validation_start": BOUNDARIES["test_start"]},
        {"validation_start": "2025-01-04T00:00:00Z"},
        {"test_start": BOUNDARIES["test_end"]},
        {"test_end": "2025-01-02T00:00:00Z"},
    ],
)
def test_split_rejects_unordered_boundaries(samples: pd.DataFrame, changes: dict) -> None:
    with pytest.raises(ValueError, match="Expected validation_start"):
        split_by_time(samples, **{**BOUNDARIES, **changes})


@pytest.mark.parametrize("column", ["observed_at", "target_at"])
@pytest.mark.parametrize("kind", ["string", "naive", "other_timezone"])
def test_split_rejects_wrong_time_dtype(samples: pd.DataFrame, column: str, kind: str) -> None:
    if kind == "string":
        samples[column] = samples[column].astype("string")
    elif kind == "naive":
        samples[column] = samples[column].dt.tz_localize(None)
    else:
        samples[column] = samples[column].dt.tz_convert("Asia/Seoul")
    with pytest.raises((TypeError, ValueError), match="timezone-aware|must use UTC"):
        split_by_time(samples, **BOUNDARIES)


@pytest.mark.parametrize("column", ["station_id", "observed_at", "target_at", *FEATURE_COLUMNS, TARGET_COLUMN])
def test_split_rejects_incomplete_samples(samples: pd.DataFrame, column: str) -> None:
    samples.loc[0, column] = pd.NA
    with pytest.raises(ValueError, match="missing values"):
        split_by_time(samples, **BOUNDARIES)


@pytest.mark.parametrize("hours", [-1, 5, 7])
def test_split_rejects_wrong_prediction_horizon(samples: pd.DataFrame, hours: int) -> None:
    samples.loc[0, "target_at"] = samples.loc[0, "observed_at"] + pd.Timedelta(hours=hours)
    with pytest.raises(ValueError, match="exactly 6 hours"):
        split_by_time(samples, **BOUNDARIES)


def test_split_rejects_duplicate_keys(samples: pd.DataFrame) -> None:
    duplicated = pd.concat([samples, samples.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate observation keys"):
        split_by_time(duplicated, **BOUNDARIES)


@pytest.mark.parametrize("name,day", [("train", 1), ("validation", 2), ("test", 3)])
def test_split_rejects_empty_partitions(samples: pd.DataFrame, name: str, day: int) -> None:
    # 해당 날짜의 표본을 없애 분할 결과가 비어 버리는 상황을 재현한다.
    reduced = samples.loc[~samples["observed_at"].dt.day.eq(day)]
    with pytest.raises(ValueError, match=f"{name} split is empty"):
        split_by_time(reduced, **BOUNDARIES)


@pytest.mark.parametrize("function", [select_complete_samples, split_by_time])
@pytest.mark.parametrize("column", ["station_id", "observed_at", "target_at", *FEATURE_COLUMNS, TARGET_COLUMN])
def test_selection_and_split_reject_missing_columns(samples: pd.DataFrame, function, column: str) -> None:
    arguments = BOUNDARIES if function is split_by_time else {}
    with pytest.raises(ValueError, match="missing columns"):
        function(samples.drop(columns=column), **arguments)


@pytest.mark.parametrize("function", [select_complete_samples, split_by_time])
def test_selection_and_split_reject_empty_inputs(samples: pd.DataFrame, function) -> None:
    arguments = BOUNDARIES if function is split_by_time else {}
    with pytest.raises(ValueError, match="No dataset rows|No complete samples"):
        function(samples.iloc[:0], **arguments)


def test_synthetic_pipeline_integration(tmp_path: Path) -> None:
    """임시 CSV와 메타데이터부터 시간 분할까지 실제 연결을 검증한다."""
    path, _ = write_processed_pair(tmp_path, make_observations(periods=96))
    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        observations, _ = load_processed_data(path)
    features = build_features(observations)
    dataset = attach_targets(features, observations)
    complete, selection_report = select_complete_samples(dataset)
    splits, split_report = split_by_time(complete, **BOUNDARIES)

    assert features.shape == (96, 9)
    assert dataset.shape == (96, 11)
    assert selection_report["usable_rows"] == 84
    assert selection_report["excluded_rows"] == 12
    assert split_report["split_rows"] == {"train": 12, "validation": 18, "test": 18}
    assert split_report["purged_boundary_rows"] == {"train": 6, "validation": 6, "test": 6}
    assert split_report["outside_period_rows"] == 18
    assert split_report["excluded_rows"] == 36
    assert TARGET_COLUMN not in FEATURE_COLUMNS
    for frame in splits.values():
        assert not frame[list(FEATURE_COLUMNS) + [TARGET_COLUMN]].isna().any().any()
        assert frame.attrs["source_timezone_status"] == "caller_supplied_unverified"


def test_real_annual_csv_pipeline_integration() -> None:
    """고정 기대값과 독립적인 시각 조회로 로컬 연간 데이터를 검증한다."""
    root = Path(__file__).resolve().parents[1]
    path = root / "data/processed/buoy_22101_2025_utc_assumed.csv"
    metadata_path = path.with_suffix(".metadata.json")
    if not path.is_file() or not metadata_path.is_file():
        pytest.skip("연간 정제 CSV와 메타데이터가 로컬에 없어 검증하지 못함")

    source_path = root / "data/preview/OBS_BUOY_TIM_20261021192756.csv"
    files = [path, metadata_path] + ([source_path] if source_path.is_file() else [])
    before = {file: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}

    with pytest.warns(UserWarning, match="Source timezone is not confirmed"):
        observations, _ = load_processed_data(path)
    features = build_features(observations)
    dataset = attach_targets(features, observations)
    complete, selection_report = select_complete_samples(dataset)
    splits, split_report = split_by_time(
        complete,
        validation_start="2025-10-01T00:00:00Z",
        test_start="2025-12-01T00:00:00Z",
        test_end="2026-01-01T00:00:00Z",
    )

    assert observations.shape == (8043, 10)
    assert features.shape == (8043, 9)
    assert dataset.shape == (8043, 11)
    assert complete.shape == (5756, 11)
    assert selection_report["excluded_rows"] == 2287
    assert split_report["split_rows"] == {"train": 3891, "validation": 1130, "test": 728}
    assert split_report["purged_boundary_rows"] == {"train": 1, "validation": 6, "test": 0}
    assert split_report["excluded_rows"] == 7

    # 결합 구현을 그대로 복제하지 않고 관측 키로 기대 파고를 직접 조회한다.
    lookup = observations.set_index(["station_id", "observed_at"])["significant_wave_height_m"]
    for hours in (1, 3, 6):
        keys = pd.MultiIndex.from_arrays([
            features["station_id"], features["observed_at"] - pd.Timedelta(hours=hours),
        ])
        expected = lookup.reindex(keys).reset_index(drop=True)
        assert_series_equal(features[f"wave_height_lag_{hours}h_m"], expected, check_names=False)
    target_keys = pd.MultiIndex.from_arrays([dataset["station_id"], dataset["target_at"]])
    expected_targets = lookup.reindex(target_keys).reset_index(drop=True)
    assert_series_equal(dataset[TARGET_COLUMN], expected_targets, check_names=False)

    for frame in splits.values():
        assert not frame[list(FEATURE_COLUMNS) + [TARGET_COLUMN]].isna().any().any()
        assert frame["target_at"].lt(pd.Timestamp(frame.attrs["split_end"])).all()
        assert frame.attrs["source_timezone_status"] == "caller_supplied_unverified"
    after = {file: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
    assert before == after
