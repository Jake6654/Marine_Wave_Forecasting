from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from marine_forecast.data.csv_loader import (
    normalize_buoy_data,
    read_buoy_csv,
)


@pytest.fixture
def source_frame() -> pd.DataFrame:
    """각 테스트에 독립적인 원본 형식 데이터를 제공한다."""
    return pd.DataFrame(
        {
            "지점": ["00123", "00123"],
            "일시": [
                "2025-01-01 01:00",
                "2025-01-01 02:00",
            ],
            "유의파고(m)": [".5", ".7"],
            "풍속(m/s)": ["6.3", "7.0"],
        },
        dtype="string",
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("풍속(m/s)", "-0.1"),
        ("유의파고(m)", "-0.1"),
        ("평균파고(m)", "-0.1"),
        ("습도(%)", "-0.1"),
        ("습도(%)", "100.1"),
        ("풍향(deg)", "-0.1"),
        ("풍향(deg)", "360.1"),
        ("현지기압(hPa)", "0"),
        ("현지기압(hPa)", "-0.1"),
    ],
)
def test_rejects_out_of_range_values(
    source_frame: pd.DataFrame,
    column: str,
    value: str,
) -> None:
    # 선택 열이 없으면 만들고, 두 번째 레코드에만 오류를 넣는다.
    if column not in source_frame.columns:
        source_frame[column] = pd.Series(
            pd.NA,
            index=source_frame.index,
            dtype="string",
        )

    source_frame.loc[1, column] = value

    with pytest.raises(ValueError, match="expected") as caught:
        normalize_buoy_data(
            source_frame,
            source_timezone="UTC",
            source_name="fixture.csv",
        )

    # 정렬 전 원본 위치와 원본 값을 보고하는지 확인한다.
    message = str(caught.value)
    assert "fixture.csv: CSV record 3" in message
    assert repr(column) in message
    assert repr(value) in message


@pytest.mark.parametrize(
    ("column", "value", "target_column"),
    [
        ("풍속(m/s)", "0", "wind_speed_mps"),
        ("유의파고(m)", "0", "significant_wave_height_m"),
        ("평균파고(m)", "0", "mean_wave_height_m"),
        ("습도(%)", "0", "humidity_pct"),
        ("습도(%)", "100", "humidity_pct"),
        ("풍향(deg)", "0", "wind_direction_deg"),
        ("풍향(deg)", "360", "wind_direction_deg"),
        ("현지기압(hPa)", "0.1", "station_pressure_hpa"),
        ("기온(°C)", "-10", "air_temperature_c"),
        ("수온(°C)", "-2", "water_temperature_c"),
    ],
)
def test_accepts_boundaries_and_negative_temperatures(
    source_frame: pd.DataFrame,
    column: str,
    value: str,
    target_column: str,
) -> None:
    # 정상 경계값이 잘못 거부되거나 변경되지 않는지 확인한다.
    source_frame[column] = value

    result = normalize_buoy_data(
        source_frame,
        source_timezone="UTC",
    )

    assert result[target_column].eq(float(value)).all()


def test_preserves_missing_measurements(
    source_frame: pd.DataFrame,
) -> None:
    columns = {
        "풍속(m/s)": "wind_speed_mps",
        "유의파고(m)": "significant_wave_height_m",
        "평균파고(m)": "mean_wave_height_m",
        "습도(%)": "humidity_pct",
        "풍향(deg)": "wind_direction_deg",
        "현지기압(hPa)": "station_pressure_hpa",
    }

    for source_column in columns:
        source_frame[source_column] = ""

    result = normalize_buoy_data(
        source_frame,
        source_timezone="UTC",
    )

    # 범위 검사가 결측 행을 삭제하거나 숫자 0으로 바꾸면 안 된다.
    assert len(result) == len(source_frame)
    assert result[list(columns.values())].isna().all().all()


@pytest.mark.parametrize("second_wave", [".5", ".9"])
def test_rejects_duplicate_observations(
    source_frame: pd.DataFrame,
    second_wave: str,
) -> None:
    source_frame.loc[1, "일시"] = source_frame.loc[0, "일시"]
    source_frame.loc[1, "유의파고(m)"] = second_wave

    # 관측값이 같거나 다르거나 동일 관측소·시각 중복은 거부한다.
    with pytest.raises(
        ValueError,
        match="duplicate station_id and observed_at",
    ) as caught:
        normalize_buoy_data(
            source_frame,
            source_timezone="UTC",
            source_name="duplicate.csv",
        )

    assert "CSV record 2" in str(caught.value)


def test_accepts_same_time_at_different_stations(
    source_frame: pd.DataFrame,
) -> None:
    source_frame.loc[1, "지점"] = "00456"
    source_frame.loc[1, "일시"] = source_frame.loc[0, "일시"]

    result = normalize_buoy_data(
        source_frame,
        source_timezone="UTC",
    )

    assert len(result) == 2
    assert result["station_id"].tolist() == ["00123", "00456"]


def test_sorts_without_changing_input(
    source_frame: pd.DataFrame,
) -> None:
    other_station = source_frame.iloc[[0]].copy()
    other_station["지점"] = "00001"

    # 시각과 관측소 순서를 섞어 실제 정렬 동작을 검사한다.
    frame = pd.concat(
        [source_frame.iloc[[1, 0]], other_station],
        ignore_index=True,
    )
    original = frame.copy(deep=True)

    result = normalize_buoy_data(
        frame,
        source_timezone="UTC",
        source_name="fixture.csv",
    )

    assert_frame_equal(frame, original)
    assert result["station_id"].tolist() == [
        "00001",
        "00123",
        "00123",
    ]
    assert result["observed_at"].tolist() == [
        pd.Timestamp("2025-01-01 01:00:00Z"),
        pd.Timestamp("2025-01-01 01:00:00Z"),
        pd.Timestamp("2025-01-01 02:00:00Z"),
    ]
    assert result.index.tolist() == [0, 1, 2]
    assert result.attrs["source_name"] == "fixture.csv"


def test_real_csv_read_normalize_and_validate() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "raw"
        / "buoy_sample.csv"
    )

    if not source.is_file():
        pytest.skip("로컬 샘플 CSV가 없어 통합 테스트를 건너뛴다")

    raw = read_buoy_csv(source)

    # 연결 검사용으로 UTC를 지정하며, 원본의 실제 시간대를 단정하지 않는다.
    result = normalize_buoy_data(
        raw,
        source_timezone="UTC",
        source_name=str(source),
    )

    assert len(result) == len(raw)
    assert not result.duplicated(
        subset=["station_id", "observed_at"],
    ).any()

    expected = result.sort_values(
        ["station_id", "observed_at"],
    ).reset_index(drop=True)

    assert_frame_equal(result, expected)
