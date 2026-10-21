import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from marine_forecast.features import (
    FEATURE_COLUMNS,
    HORIZON_HOURS,
    LAG_HOURS,
    REQUIRED_OBSERVATION_COLUMNS,
    TARGET_COLUMN,
    build_features,
)


@pytest.fixture
def observations() -> pd.DataFrame:
    """행 번호와 파고가 대응하는 독립적인 시간별 관측을 만든다."""
    frame = pd.DataFrame(
        {
            "station_id": pd.Series(["00123"] * 10, dtype="string"),
            "observed_at": pd.date_range("2025-01-01", periods=10, freq="h", tz="UTC"),
            "significant_wave_height_m": pd.Series(range(10), dtype="Float64"),
            "wind_speed_mps": pd.Series([0.0] * 10, dtype="Float64"),
            "station_pressure_hpa": pd.Series([1000.0] * 10, dtype="Float64"),
        }
    )
    frame.attrs = {"source_timezone": "UTC", "source_timezone_status": "caller_supplied_unverified"}
    return frame


def test_feature_contract_excludes_target_and_identifiers() -> None:
    assert HORIZON_HOURS == 6
    assert LAG_HOURS == (1, 3, 6)
    assert len(FEATURE_COLUMNS) == len(set(FEATURE_COLUMNS)) == 7
    assert not {"station_id", "observed_at", "target_at", TARGET_COLUMN}.intersection(FEATURE_COLUMNS)


@pytest.mark.parametrize("hours", [1, 3, 6])
def test_connects_exact_lag_on_continuous_data(observations: pd.DataFrame, hours: int) -> None:
    result = build_features(observations)
    expected = pd.Series([pd.NA] * hours + list(range(10 - hours)), dtype="Float64")

    assert_series_equal(result[f"wave_height_lag_{hours}h_m"], expected, check_names=False)
    assert len(result) == len(observations)


def test_change_preserves_zero_and_propagates_missing_values(observations: pd.DataFrame) -> None:
    observations.loc[4, "significant_wave_height_m"] = pd.NA
    result = build_features(observations)

    assert result.loc[0, "significant_wave_height_m"] == 0
    assert result.loc[0, "wind_speed_mps"] == 0
    assert pd.isna(result.loc[0, "wave_height_change_1h_m"])
    assert result.loc[1, "wave_height_change_1h_m"] == 1
    assert pd.isna(result.loc[4, "wave_height_change_1h_m"])
    assert pd.isna(result.loc[5, "wave_height_change_1h_m"])


def test_missing_hour_does_not_use_previous_row(observations: pd.DataFrame) -> None:
    observations = observations.drop(index=2).reset_index(drop=True)
    result = build_features(observations).set_index("observed_at")
    row = result.loc[pd.Timestamp("2025-01-01T03:00:00Z")]

    # 02시가 없으므로 01시 값을 1시간 전 값으로 가져오면 안 된다.
    assert pd.isna(row["wave_height_lag_1h_m"])
    assert row["wave_height_lag_3h_m"] == 0
    assert len(result) == 9


def test_never_mixes_stations_and_sorts_results(observations: pd.DataFrame) -> None:
    other = observations.copy(deep=True)
    other["station_id"] = pd.Series(["00456"] * len(other), dtype="string")
    other["significant_wave_height_m"] += 100
    combined = pd.concat([other, observations], ignore_index=True).sample(frac=1, random_state=42)
    original = combined.copy(deep=True)

    result = build_features(combined)

    assert_frame_equal(combined, original)
    assert result.shape == (20, 9)
    assert result.columns.tolist() == ["station_id", "observed_at"] + list(FEATURE_COLUMNS)
    assert_frame_equal(result, result.sort_values(["station_id", "observed_at"]).reset_index(drop=True))
    for station, expected in (("00123", 0.0), ("00456", 100.0)):
        row = result.loc[(result["station_id"] == station) & (result["observed_at"] == pd.Timestamp("2025-01-01T06:00:00Z"))].iloc[0]
        assert row["wave_height_lag_6h_m"] == expected


def test_future_changes_cannot_change_past_features(observations: pd.DataFrame) -> None:
    before = build_features(observations)
    changed = observations.copy(deep=True)
    changed.loc[8:, "significant_wave_height_m"] = 999
    after = build_features(changed)
    cutoff = pd.Timestamp("2025-01-01T08:00:00Z")

    assert_frame_equal(before.loc[before["observed_at"] < cutoff], after.loc[after["observed_at"] < cutoff])


def test_lags_cross_midnight_and_preserve_input_metadata(observations: pd.DataFrame) -> None:
    observations["observed_at"] = pd.date_range("2025-01-01T22:00:00Z", periods=10, freq="h")
    original = observations.copy(deep=True)

    result = build_features(observations)

    assert result.loc[6, "observed_at"] == pd.Timestamp("2025-01-02T04:00:00Z")
    assert result.loc[6, "wave_height_lag_6h_m"] == 0
    assert result.attrs == original.attrs
    assert_frame_equal(observations, original)


@pytest.mark.parametrize("column", REQUIRED_OBSERVATION_COLUMNS)
def test_rejects_missing_columns(observations: pd.DataFrame, column: str) -> None:
    with pytest.raises(ValueError, match="Missing observation columns"):
        build_features(observations.drop(columns=column))


def test_rejects_empty_observations(observations: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="No observations"):
        build_features(observations.iloc[:0])


@pytest.mark.parametrize("column", ["station_id", "observed_at"])
def test_rejects_missing_keys(observations: pd.DataFrame, column: str) -> None:
    observations.loc[0, column] = pd.NA
    with pytest.raises(ValueError, match="keys must not be missing"):
        build_features(observations)


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_rejects_blank_station(observations: pd.DataFrame, value: str) -> None:
    observations.loc[0, "station_id"] = value
    with pytest.raises(ValueError, match="station_id must not be empty"):
        build_features(observations)


def test_rejects_numeric_station_dtype(observations: pd.DataFrame) -> None:
    observations["station_id"] = 123
    with pytest.raises(TypeError, match="string dtype"):
        build_features(observations)


@pytest.mark.parametrize("kind", ["string", "naive", "other_timezone"])
def test_rejects_wrong_time_dtype(observations: pd.DataFrame, kind: str) -> None:
    if kind == "string":
        observations["observed_at"] = observations["observed_at"].astype("string")
    elif kind == "naive":
        observations["observed_at"] = observations["observed_at"].dt.tz_localize(None)
    else:
        observations["observed_at"] = observations["observed_at"].dt.tz_convert("Asia/Seoul")

    with pytest.raises((TypeError, ValueError), match="timezone-aware|must use UTC"):
        build_features(observations)


def test_rejects_duplicate_keys(observations: pd.DataFrame) -> None:
    duplicated = pd.concat([observations, observations.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate"):
        build_features(duplicated)


@pytest.mark.parametrize("column", REQUIRED_OBSERVATION_COLUMNS[2:])
def test_rejects_numeric_strings(observations: pd.DataFrame, column: str) -> None:
    observations[column] = observations[column].astype("string")
    with pytest.raises(TypeError, match="numeric dtype"):
        build_features(observations)


@pytest.mark.parametrize("column", REQUIRED_OBSERVATION_COLUMNS[2:])
@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_rejects_infinite_values(observations: pd.DataFrame, column: str, value: float) -> None:
    observations.loc[0, column] = value
    with pytest.raises(ValueError, match="infinity"):
        build_features(observations)
