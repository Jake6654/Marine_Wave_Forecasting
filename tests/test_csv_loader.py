import hashlib
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from marine_forecast.data.csv_loader import (
    COLUMN_NAMES,
    _reject_invalid_values,
    normalize_buoy_data,
    read_buoy_csv,
)


@pytest.fixture
def source_frame() -> pd.DataFrame:
    """숫자, 빈 값, 0과 추가 열을 포함한 독립적인 입력을 제공한다."""
    return pd.DataFrame(
        {
            "지점": [" 00123 ", "00123", "00123"],
            "일시": [
                "2025-01-01 01:00",
                "2025-01-01 02:00",
                "2025-01-01 03:00",
            ],
            "유의파고(m)": [" .5 ", "   ", "0"],
            "풍속(m/s)": ["6.3", "", "0"],
            "비고": ["첫 관측", "두 번째", "세 번째"],
        },
        dtype="string",
    )


def test_read_preserves_source_strings(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text(
        "지점,일시,유의파고(m)\n"
        "00123,2025-01-01 01:00,.5\n"
        "00123,2025-01-01 02:00,NA\n",
        encoding="cp949",
    )

    frame = read_buoy_csv(path)

    assert frame.shape == (2, 3)
    assert frame["지점"].tolist() == ["00123", "00123"]
    assert frame["유의파고(m)"].tolist() == [".5", "NA"]


@pytest.mark.parametrize("column", ["지점", "일시", "유의파고(m)"])
def test_read_rejects_missing_required_column(tmp_path: Path, column: str) -> None:
    path = tmp_path / "missing_column.csv"
    columns = [name for name in ("지점", "일시", "유의파고(m)") if name != column]
    path.write_text(
        ",".join(columns) + "\n" + ",".join("value" for _ in columns) + "\n",
        encoding="cp949",
    )

    with pytest.raises(ValueError, match="missing required columns") as caught:
        read_buoy_csv(path)

    assert column in str(caught.value)
    assert str(path) in str(caught.value)


def test_read_rejects_header_only_file(tmp_path: Path) -> None:
    path = tmp_path / "header_only.csv"
    path.write_text("지점,일시,유의파고(m)\n", encoding="cp949")

    with pytest.raises(ValueError, match="no observation records"):
        read_buoy_csv(path)


def test_read_rejects_wrong_encoding(tmp_path: Path) -> None:
    path = tmp_path / "encoded.csv"
    path.write_text("지점,일시,유의파고(m)\n", encoding="cp949")

    with pytest.raises(UnicodeDecodeError):
        read_buoy_csv(path, encoding="utf-8")


def test_normalize_preserves_input_and_builds_schema(source_frame: pd.DataFrame) -> None:
    original = source_frame.copy(deep=True)

    result = normalize_buoy_data(
        source_frame, source_timezone="Asia/Seoul", source_name="fixture.csv"
    )

    assert_frame_equal(source_frame, original)
    assert result.shape == (3, 10)
    assert result.columns.tolist() == list(COLUMN_NAMES.values())
    assert result["station_id"].tolist() == ["00123"] * 3
    assert result.loc[0, "observed_at"] == pd.Timestamp("2024-12-31 16:00:00Z")
    assert str(result["observed_at"].dt.tz) == "UTC"
    assert result.loc[0, "significant_wave_height_m"] == 0.5
    assert pd.isna(result.loc[1, "significant_wave_height_m"])
    assert result.loc[2, "significant_wave_height_m"] == 0.0
    assert result.loc[2, "wind_speed_mps"] == 0.0
    assert result["water_temperature_c"].isna().all()
    for column in list(COLUMN_NAMES.values())[2:]:
        assert str(result[column].dtype) == "Float64"
    assert result.attrs["unmapped_columns"] == ["비고"]
    assert result.attrs["source_timezone"] == "Asia/Seoul"
    assert result.attrs["source_name"] == "fixture.csv"


def test_normalize_uses_explicit_timezone(source_frame: pd.DataFrame) -> None:
    result = normalize_buoy_data(source_frame, source_timezone="UTC")

    assert result.loc[0, "observed_at"] == pd.Timestamp("2025-01-01 01:00:00Z")


@pytest.mark.parametrize("column", ["지점", "일시", "유의파고(m)"])
def test_normalize_rejects_missing_required_column(
    source_frame: pd.DataFrame, column: str
) -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        normalize_buoy_data(source_frame.drop(columns=column), source_timezone="UTC")


def test_normalize_rejects_empty_frame(source_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="no observation records"):
        normalize_buoy_data(source_frame.iloc[:0], source_timezone="UTC")


@pytest.mark.parametrize("column", ["지점", "일시"])
@pytest.mark.parametrize("value", ["", "   ", pd.NA])
def test_normalize_rejects_empty_keys(
    source_frame: pd.DataFrame, column: str, value: object
) -> None:
    source_frame.loc[1, column] = value

    with pytest.raises(ValueError, match="a non-empty value is required") as caught:
        normalize_buoy_data(
            source_frame, source_timezone="UTC", source_name="fixture.csv"
        )

    assert "fixture.csv: CSV record 3" in str(caught.value)
    assert repr(column) in str(caught.value)


@pytest.mark.parametrize("value", ["", "   ", pd.NA])
def test_normalize_accepts_missing_measurement(
    source_frame: pd.DataFrame, value: object
) -> None:
    source_frame.loc[0, "유의파고(m)"] = value

    result = normalize_buoy_data(source_frame, source_timezone="UTC")

    assert pd.isna(result.loc[0, "significant_wave_height_m"])
    assert len(result) == 3


@pytest.mark.parametrize(
    "value", ["측정실패", "NaN", "NA", "inf", "-inf", "Infinity", "1e999"]
)
def test_normalize_rejects_nonfinite_or_invalid_measurement(
    source_frame: pd.DataFrame, value: str
) -> None:
    source_frame.loc[1, "유의파고(m)"] = value

    with pytest.raises(ValueError, match="expected a finite number") as caught:
        normalize_buoy_data(
            source_frame, source_timezone="UTC", source_name="fixture.csv"
        )

    message = str(caught.value)
    assert "CSV record 3" in message
    assert "column '유의파고(m)'" in message
    assert repr(value) in message


@pytest.mark.parametrize(
    "value",
    ["2025-02-30 01:00", "2025/01/01 01:00", "2025-1-1 01:00", "2025-01-01 25:00"],
)
def test_normalize_rejects_invalid_timestamp(
    source_frame: pd.DataFrame, value: str
) -> None:
    source_frame.loc[1, "일시"] = value

    with pytest.raises(ValueError, match="expected a valid timestamp") as caught:
        normalize_buoy_data(source_frame, source_timezone="UTC")

    assert "CSV record 3" in str(caught.value)
    assert repr(value) in str(caught.value)


@pytest.mark.parametrize("timezone", ["", "Not/A_Zone"])
def test_normalize_rejects_invalid_timezone(
    source_frame: pd.DataFrame, timezone: str
) -> None:
    with pytest.raises(ValueError, match="invalid source timezone"):
        normalize_buoy_data(source_frame, source_timezone=timezone)


def test_error_report_uses_first_invalid_record() -> None:
    raw = pd.DataFrame({"유의파고(m)": [".5", "first", "second"]})
    mask = pd.Series([False, True, True])

    with pytest.raises(ValueError, match="CSV record 3") as caught:
        _reject_invalid_values(raw, "유의파고(m)", mask, "fixture.csv", "bad value")

    assert "value 'first'" in str(caught.value)
    assert "second" not in str(caught.value)


def test_error_report_accepts_mask_without_errors() -> None:
    raw = pd.DataFrame({"유의파고(m)": [".5", ""]})
    mask = pd.Series([False, pd.NA], dtype="boolean")

    assert _reject_invalid_values(raw, "유의파고(m)", mask, "fixture.csv", "bad") is None


def test_real_csv_read_and_normalize() -> None:
    source = Path(__file__).resolve().parents[1] / "data" / "raw" / "buoy_sample.csv"
    if not source.is_file():
        pytest.skip("로컬 샘플 CSV가 없어 실제 파일 통합 테스트를 건너뛴다")

    before = hashlib.sha256(source.read_bytes()).hexdigest()
    raw = read_buoy_csv(source)

    # UTC를 입력 시간대로 지정해 변환 연결을 검사하며 실제 CSV 시간대를 단정하지 않는다.
    result = normalize_buoy_data(raw, source_timezone="UTC", source_name=str(source))

    assert len(result) == len(raw)
    assert result.columns.tolist() == list(COLUMN_NAMES.values())
    assert result.loc[0, "station_id"] == raw.loc[0, "지점"].strip()
    assert result.loc[0, "significant_wave_height_m"] == float(raw.loc[0, "유의파고(m)"])
    assert result.loc[0, "observed_at"] == pd.Timestamp(raw.loc[0, "일시"]).tz_localize("UTC")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
