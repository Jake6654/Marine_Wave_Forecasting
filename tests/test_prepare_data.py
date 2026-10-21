import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from marine_forecast.data.csv_loader import COLUMN_NAMES, normalize_buoy_data, read_buoy_csv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prepare_data.py"


@pytest.fixture
def preparation() -> ModuleType:
    # import 시 main이 실행되지 않도록 스크립트를 독립 모듈로 읽는다.
    spec = importlib.util.spec_from_file_location("prepare_data_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source_csv(tmp_path: Path) -> Path:
    source = tmp_path / "original.csv"
    source.write_text(
        "지점,일시,풍속(m/s),기온(°C),유의파고(m),비고\n"
        "00123,2025-01-01 02:00,0,-2,0,정상\n"
        "00123,2025-01-01 01:00,6.3,,.5,확인\n"
        "00123,2025-01-01 03:00,,, ,결측\n",
        encoding="cp949",
    )
    return source


def arguments(source: Path, output: Path) -> list[str]:
    return ["--input", str(source), "--output", str(output), "--source-timezone", "UTC"]


def prepare(module: ModuleType, source: Path, output: Path) -> dict[str, object]:
    return module.prepare_data(
        source, output, encoding="cp949", source_timezone="UTC"
    )


def test_parse_args_builds_paths_and_defaults(preparation: ModuleType) -> None:
    args = preparation.parse_args(arguments(Path("source.csv"), Path("output.csv")))

    assert args.input == Path("source.csv")
    assert args.output == Path("output.csv")
    assert args.encoding == "cp949"
    assert args.source_timezone == "UTC"


def test_parse_args_accepts_explicit_encoding(preparation: ModuleType) -> None:
    args = preparation.parse_args(
        arguments(Path("source.csv"), Path("output.csv")) + ["--encoding", "utf-8"]
    )

    assert args.encoding == "utf-8"


@pytest.mark.parametrize("option", ["--input", "--output", "--source-timezone"])
def test_parse_args_requires_explicit_options(preparation: ModuleType, option: str) -> None:
    argv = arguments(Path("source.csv"), Path("output.csv"))
    position = argv.index(option)
    del argv[position:position + 2]

    with pytest.raises(SystemExit) as caught:
        preparation.parse_args(argv)

    assert caught.value.code == 2


def test_parse_args_help_exits_successfully(preparation: ModuleType, capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        preparation.parse_args(["--help"])

    assert caught.value.code == 0
    assert "--source-timezone" in capsys.readouterr().out


@pytest.mark.parametrize("collision", ["csv", "metadata"])
def test_rejects_source_output_collision(
    preparation: ModuleType, source_csv: Path, tmp_path: Path, collision: str
) -> None:
    output = source_csv if collision == "csv" else tmp_path / "result.csv"
    source = source_csv
    if collision == "metadata":
        source = output.with_suffix(".metadata.json")
        source.write_bytes(source_csv.read_bytes())
    before = source.read_bytes()

    with pytest.raises(ValueError, match="must be different"):
        prepare(preparation, source, output)

    assert source.read_bytes() == before


def test_rejects_missing_source(preparation: ModuleType, tmp_path: Path) -> None:
    output = tmp_path / "processed" / "result.csv"

    with pytest.raises(FileNotFoundError, match="Input CSV not found"):
        prepare(preparation, tmp_path / "absent.csv", output)

    assert not output.parent.exists()


def test_rejects_non_csv_output(
    preparation: ModuleType, source_csv: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match=".csv extension"):
        prepare(preparation, source_csv, tmp_path / "result.json")


@pytest.mark.parametrize("existing_kind", ["csv", "metadata"])
def test_preserves_existing_outputs(
    preparation: ModuleType, source_csv: Path, tmp_path: Path, existing_kind: str
) -> None:
    output = tmp_path / "result.csv"
    metadata_path = output.with_suffix(".metadata.json")
    existing = output if existing_kind == "csv" else metadata_path
    existing.write_bytes(b"previous result")

    with pytest.raises(FileExistsError, match="Output already exists"):
        prepare(preparation, source_csv, output)

    assert existing.read_bytes() == b"previous result"
    assert not (metadata_path if existing_kind == "csv" else output).exists()


def test_saves_and_reloads_csv_and_metadata(
    preparation: ModuleType, source_csv: Path, tmp_path: Path
) -> None:
    output = tmp_path / "processed" / "nested" / "result.csv"
    before = source_csv.read_bytes()

    metadata = prepare(preparation, source_csv, output)
    saved_metadata = json.loads(output.with_suffix(".metadata.json").read_text("utf-8"))

    assert metadata == saved_metadata
    assert metadata["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert metadata["source_path"] == str(source_csv.resolve())
    assert metadata["source_encoding"] == "cp949"
    assert metadata["source_timezone"] == "UTC"
    assert metadata["source_timezone_status"] == "caller_supplied_unverified"
    assert metadata["output_timezone"] == "UTC"
    assert metadata["output_path"] == str(output.resolve())
    assert metadata["metadata_path"] == str(output.with_suffix(".metadata.json").resolve())
    assert metadata["rows"] == 3
    assert metadata["columns"] == list(COLUMN_NAMES.values())
    assert metadata["unmapped_columns"] == ["비고"]
    assert metadata["missing_counts"]["significant_wave_height_m"] == 1
    assert metadata["first_observed_at"] == "2025-01-01T01:00:00+00:00"
    assert metadata["last_observed_at"] == "2025-01-01T03:00:00+00:00"

    # CSV를 다시 읽을 때 식별자·실수·시간대의 자료형을 명시적으로 복원한다.
    numeric_columns = list(COLUMN_NAMES.values())[2:]
    restored = pd.read_csv(
        output,
        encoding="utf-8",
        dtype={"station_id": "string", **{c: "Float64" for c in numeric_columns}},
    )
    restored["observed_at"] = pd.to_datetime(restored["observed_at"], utc=True)
    expected = normalize_buoy_data(read_buoy_csv(source_csv), source_timezone="UTC")
    assert_frame_equal(restored, expected, check_dtype=False)
    assert restored["station_id"].tolist() == ["00123"] * 3
    assert int(restored["significant_wave_height_m"].eq(0).sum()) == 1
    assert source_csv.read_bytes() == before


@pytest.mark.parametrize(
    ("encoding", "timezone", "exception"),
    [
        ("utf-8", "UTC", UnicodeDecodeError),
        ("not-an-encoding", "UTC", LookupError),
        ("cp949", "Not/A_Zone", ValueError),
    ],
)
def test_rejects_bad_settings_before_saving(
    preparation: ModuleType, source_csv: Path, tmp_path: Path,
    encoding: str, timezone: str, exception: type[Exception],
) -> None:
    output = tmp_path / "processed" / "result.csv"

    with pytest.raises(exception):
        preparation.prepare_data(
            source_csv, output, encoding=encoding, source_timezone=timezone
        )

    assert not output.parent.exists()


def test_rejects_invalid_observation_before_saving(
    preparation: ModuleType, source_csv: Path, tmp_path: Path
) -> None:
    source_csv.write_text(
        "지점,일시,유의파고(m)\n00123,2025-01-01 01:00,-1\n", encoding="cp949"
    )
    output = tmp_path / "processed" / "result.csv"

    with pytest.raises(ValueError, match="expected significant wave height"):
        prepare(preparation, source_csv, output)

    assert not output.parent.exists()


def test_rejects_source_changed_during_read(
    preparation: ModuleType, source_csv: Path, tmp_path: Path, monkeypatch
) -> None:
    original_reader = preparation.read_buoy_csv
    output = tmp_path / "processed" / "result.csv"

    def read_then_change(path: Path, *, encoding: str):
        frame = original_reader(path, encoding=encoding)
        # 임시 파일에서만 원본 변경을 재현하고 실제 연간 CSV는 건드리지 않는다.
        path.write_bytes(path.read_bytes() + b"\n")
        return frame

    monkeypatch.setattr(preparation, "read_buoy_csv", read_then_change)

    with pytest.raises(ValueError, match="Source CSV changed during preparation"):
        prepare(preparation, source_csv, output)

    assert not output.exists()
    assert not output.with_suffix(".metadata.json").exists()


@pytest.mark.parametrize("failure_stage", ["csv", "metadata"])
def test_cleans_partial_outputs_after_write_failure(
    preparation: ModuleType, source_csv: Path, tmp_path: Path,
    monkeypatch, failure_stage: str,
) -> None:
    output = tmp_path / "processed" / "result.csv"
    before = source_csv.read_bytes()
    original_error = OSError("simulated write failure")

    if failure_stage == "csv":
        def fail_csv(self, handle, **kwargs):
            handle.write("partial CSV")
            raise original_error

        monkeypatch.setattr(pd.DataFrame, "to_csv", fail_csv)
    else:
        def fail_json(value, handle, **kwargs):
            handle.write("partial JSON")
            raise original_error

        monkeypatch.setattr(preparation.json, "dump", fail_json)

    with pytest.raises(OSError) as caught:
        prepare(preparation, source_csv, output)

    assert caught.value is original_error
    assert not output.exists()
    assert not output.with_suffix(".metadata.json").exists()
    assert source_csv.read_bytes() == before


def test_reports_cleanup_failure_without_hiding_original_error(
    preparation: ModuleType, source_csv: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    output = tmp_path / "result.csv"
    original_error = OSError("original JSON failure")
    original_unlink = Path.unlink

    def fail_json(*args, **kwargs):
        raise original_error

    def fail_output_cleanup(path, *args, **kwargs):
        if path == output:
            raise PermissionError("simulated cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(preparation.json, "dump", fail_json)
    monkeypatch.setattr(Path, "unlink", fail_output_cleanup)

    with pytest.raises(OSError) as caught:
        prepare(preparation, source_csv, output)

    assert caught.value is original_error
    assert "WARNING: Could not remove" in capsys.readouterr().err
    assert output.exists()
    assert not output.with_suffix(".metadata.json").exists()


@pytest.mark.parametrize("raced_kind", ["csv", "metadata"])
def test_exclusive_creation_preserves_racing_file(
    preparation: ModuleType, source_csv: Path, tmp_path: Path, monkeypatch, raced_kind: str
) -> None:
    output = tmp_path / "result.csv"
    metadata_path = output.with_suffix(".metadata.json")
    raced_path = output if raced_kind == "csv" else metadata_path
    original_normalizer = preparation.normalize_buoy_data

    def normalize_and_create(*args, **kwargs):
        frame = original_normalizer(*args, **kwargs)
        # 사전 존재 검사 이후 다른 실행이 결과를 만든 상황을 재현한다.
        raced_path.write_bytes(b"other process result")
        return frame

    monkeypatch.setattr(preparation, "normalize_buoy_data", normalize_and_create)

    with pytest.raises(FileExistsError):
        prepare(preparation, source_csv, output)

    assert raced_path.read_bytes() == b"other process result"
    assert not (metadata_path if raced_kind == "csv" else output).exists()


def test_main_reports_success(preparation: ModuleType, monkeypatch, capsys) -> None:
    args = argparse.Namespace(
        input=Path("source.csv"), output=Path("result.csv"),
        encoding="cp949", source_timezone="UTC",
    )
    monkeypatch.setattr(preparation, "parse_args", lambda: args)
    calls = []

    def fake_prepare(*values, **settings):
        calls.append((values, settings))
        return {"rows": 3, "output_path": "result.csv", "metadata_path": "result.metadata.json", "missing_counts": {}}

    monkeypatch.setattr(preparation, "prepare_data", fake_prepare)

    assert preparation.main() == 0
    captured = capsys.readouterr()
    assert "Prepared rows: 3" in captured.out
    assert "unverified" in captured.err
    assert calls == [((args.input, args.output), {"encoding": "cp949", "source_timezone": "UTC"})]


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), ValueError("invalid"), LookupError("encoding")])
def test_main_reports_expected_error(
    preparation: ModuleType, monkeypatch, capsys, error: Exception
) -> None:
    monkeypatch.setattr(
        preparation, "parse_args",
        lambda: argparse.Namespace(input=Path("source.csv"), output=Path("result.csv"), encoding="cp949", source_timezone="UTC"),
    )

    def fail_prepare(*args, **kwargs):
        raise error

    monkeypatch.setattr(preparation, "prepare_data", fail_prepare)

    assert preparation.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"ERROR: {error}" in captured.err


def run_cli(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # 실제 Python 프로세스로 실행하되 시간 제한과 출력 수집을 적용한다.
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT_PATH), *argv],
        cwd=cwd, capture_output=True, text=True, timeout=30, check=False,
    )


def test_cli_help_runs_outside_project(tmp_path: Path) -> None:
    completed = run_cli(["--help"], tmp_path)

    assert completed.returncode == 0
    assert "--source-timezone" in completed.stdout


def test_cli_missing_input_returns_one(tmp_path: Path) -> None:
    completed = run_cli(arguments(tmp_path / "absent.csv", tmp_path / "result.csv"), tmp_path)

    assert completed.returncode == 1
    assert "Input CSV not found" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not (tmp_path / "result.csv").exists()


def test_cli_real_annual_csv_and_repeat_protection(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "data" / "preview" / "OBS_BUOY_TIM_20261021192756.csv"
    if not source.is_file():
        pytest.skip("로컬 연간 CSV가 없어 실제 파일 통합 테스트를 건너뛴다")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "processed" / "annual_utc_assumed.csv"

    # UTC는 연결 검증용 인자이며 원본 시간대가 확인됐다는 뜻이 아니다.
    completed = run_cli(arguments(source, output), tmp_path)

    assert completed.returncode == 0, completed.stderr
    raw = read_buoy_csv(source)
    metadata_path = output.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text("utf-8"))
    numeric_columns = list(COLUMN_NAMES.values())[2:]
    restored = pd.read_csv(
        output,
        dtype={"station_id": "string", **{c: "Float64" for c in numeric_columns}},
    )
    restored["observed_at"] = pd.to_datetime(restored["observed_at"], utc=True)
    expected = normalize_buoy_data(raw, source_timezone="UTC")
    assert_frame_equal(restored, expected, check_dtype=False)
    assert len(restored) == len(raw) == metadata["rows"]
    assert metadata["source_sha256"] == before
    assert metadata["source_timezone_status"] == "caller_supplied_unverified"
    assert restored["station_id"].eq("22101").all()
    assert "unverified" in completed.stderr
    assert f"Prepared rows: {len(raw)}" in completed.stdout
    for source_column, target_column in COLUMN_NAMES.items():
        if source_column in ("지점", "일시"):
            continue
        expected_missing = int(raw[source_column].str.strip().eq("").sum())
        assert int(restored[target_column].isna().sum()) == expected_missing
        assert metadata["missing_counts"][target_column] == expected_missing

    # 재실행이 기존 결과를 덮어쓰지 않는지 바이트 비교로 확인한다.
    saved_csv = output.read_bytes()
    saved_metadata = metadata_path.read_bytes()
    repeated = run_cli(arguments(source, output), tmp_path)
    assert repeated.returncode == 1
    assert "Output already exists" in repeated.stderr
    assert output.read_bytes() == saved_csv
    assert metadata_path.read_bytes() == saved_metadata
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
