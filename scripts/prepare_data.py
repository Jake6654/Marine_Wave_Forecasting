import argparse
import hashlib
import json
import sys
from pathlib import Path

from marine_forecast.data.csv_loader import (
    normalize_buoy_data,
    read_buoy_csv,
)

# argparse 는 --input 같은 명령행 인자 해석
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """리스트를 전달하면 그 리스트를 명령행 인자로 해석 """
    parser = argparse.ArgumentParser(
        description="Validate and normalize a marine buoy CSV.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the original CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to a new normalized CSV.",
    )
    parser.add_argument(
        "--encoding",
        default="cp949",
        help="Source encoding. Default: cp949.",
    )
    parser.add_argument(
        "--source-timezone",
        required=True,
        help="Timezone used to interpret source timestamps.",
    )
    parser.add_argument(
        "--source-timezone-evidence",
        help="Caller-reported confirmation evidence; omitted means unverified.",
    )
    return parser.parse_args(argv)


# { 반환값
#     "rows": 8043,
#     "source_encoding": "cp949",
#     "source_timezone": "UTC",
#     # 나머지 처리 기록
# }

def prepare_data(
    input_path: Path,
    output_path: Path,
    *,
    encoding: str,
    source_timezone: str,
    source_timezone_evidence: str | None = None,
) -> dict[str, object]:
    """원본 검증 후 새 CSV와 처리 기록을 저장한다"""
    # 시간대 이름만 지정한 실행과 확인 근거를 전달한 실행을 구분한다.
    if source_timezone_evidence is not None:
        if not isinstance(source_timezone_evidence, str) or not source_timezone_evidence.strip():
            raise ValueError("Source timezone evidence must be a non-empty string.")
        source_timezone_evidence = source_timezone_evidence.strip()

    input_path = input_path.resolve() # 절대경로로 만듬
    output_path = output_path.resolve()
    # return a new file path with suffix changed
    # buoy_22101_2025_utc_assumed.csv ->  buoy_22101_2025_utc_assumed.metadata.json
    metadata_path = output_path.with_suffix(".metadata.json") 

    if input_path in (output_path, metadata_path):
        raise ValueError("Input and output paths must be different.")

    if not input_path.is_file():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    if output_path.suffix.lower() != ".csv":
        raise ValueError("Output path must have a .csv extension.")

    # 이전 실행 결과를 실수로 덮어쓰지 않는다.
    for path in (output_path, metadata_path):
        if path.exists():
            raise FileExistsError(f"Output already exists: {path}")

    # 원본 파일의 SHA-256 계산
    source_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()

    raw = read_buoy_csv(input_path, encoding=encoding)
    frame = normalize_buoy_data(
        raw,
        source_timezone= source_timezone,
        source_name=str(input_path),
    )

    # 처리 중 원본이 바뀌었다면 출처 기록의 일관성을 보장할 수 없다.
    current_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()

    if current_hash != source_hash:
        raise ValueError("Source CSV changed during preparation.")

    # CSV에는 자료형과 attrs가 보존되지 않으므로 별도 기록을 남긴다.
    metadata: dict[str, object] = {
        "source_path": str(input_path),
        "source_sha256": source_hash,
        "source_encoding": encoding,
        "source_timezone": source_timezone,
        "source_timezone_status": (
            "confirmed" if source_timezone_evidence is not None
            else "caller_supplied_unverified"
        ),
        "output_timezone": "UTC",
        "output_path": str(output_path),
        "metadata_path": str(metadata_path),
        "rows": len(frame),
        "columns": frame.columns.tolist(),
        "dtypes": {
            column: str(dtype)
            for column, dtype in frame.dtypes.items()
        },
        "missing_counts": {
            column: int(count)
            for column, count in frame.isna().sum().items()
        },
        "first_observed_at": frame["observed_at"].min().isoformat(),
        "last_observed_at": frame["observed_at"].max().isoformat(),
        "unmapped_columns": frame.attrs["unmapped_columns"],
    }

    # 확인은 호출자가 보고한 것이며, 프로그램이 공식 답변을 조회한 것은 아니다.
    if source_timezone_evidence is not None:
        metadata["source_timezone_evidence"] = source_timezone_evidence
        metadata["source_timezone_confirmation_method"] = "caller_reported"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_paths: list[Path] = []

    try:
        # x 모드는 검사 이후 파일이 생겼더라도 덮어쓰지 않고 실패한다
        with output_path.open(
            "x",
            encoding="utf-8",
            newline="",
        ) as output_file:
            created_paths.append(output_path)
            frame.to_csv(output_file, index=False)

        with metadata_path.open(
            "x",
            encoding="utf-8",
        ) as metadata_file:
            created_paths.append(metadata_path)
            json.dump(
                metadata,
                metadata_file,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            metadata_file.write("\n")

    except Exception:
        # 저장 실패 시 이번 실행이 생성한 파일만 정리하고 원래 오류를 다시 낸다.
        for path in reversed(created_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                print(
                    f"WARNING: Could not remove {path}: {cleanup_error}",
                    file=sys.stderr,
                )
        raise

    return metadata

def main() -> int:
    """실행 결과를 출력하고 운영체제에 전달할 종료 코드를 반환한다."""
    args = parse_args()

    confirmation_options = {}
    if args.source_timezone_evidence is not None:
        confirmation_options["source_timezone_evidence"] = args.source_timezone_evidence

    try:
        metadata = prepare_data(
            args.input,
            args.output,
            encoding=args.encoding,
            source_timezone=args.source_timezone,
            **confirmation_options,
        )
    except (OSError, ValueError, LookupError) as error:
        # 예상 가능한 입력·저장 오류
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if metadata.get("source_timezone_status") != "confirmed":
        print(
            "WARNING: Source timezone is caller-supplied and unverified.",
            file=sys.stderr,
        )
    else:
        print("Timezone confirmation: caller-reported evidence recorded.")
    print(f"Prepared rows: {metadata['rows']}")
    print(f"Output CSV: {metadata['output_path']}")
    print(f"Metadata: {metadata['metadata_path']}")
    print(f"Missing values: {metadata['missing_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
