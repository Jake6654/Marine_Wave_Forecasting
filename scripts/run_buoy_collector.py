"""수집기를 중복 실행 잠금과 함께 구동한다."""

import argparse
import os
import signal
import sys
from pathlib import Path

from marine_forecast.collector_runtime import exclusive_collector_lock, run_loop


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECT_SCRIPT = PROJECT_ROOT / "scripts" / "collect_buoy.py"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run buoy collection sequentially.")
    parser.add_argument("--station-id", default="22101")
    parser.add_argument("--lookback-hours", type=int, choices=range(1, 25), default=12)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data/runtime/buoy_observations.sqlite3")
    parser.add_argument("--interval-seconds", type=float, default=600)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--child-timeout-seconds", type=float, default=60)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--status-file", type=Path)
    return parser.parse_args(argv)


def _stop(signum, frame):
    # Docker의 SIGTERM도 Ctrl+C와 같은 정리 경로로 처리한다.
    raise KeyboardInterrupt


def main() -> int:
    args = parse_args()
    if not os.environ.get("KMA_API_KEY"):
        print("ERROR: KMA_API_KEY is not configured.", file=sys.stderr)
        return 1
    if not args.station_id.isascii() or not args.station_id.isdigit() or not args.station_id.strip("0"):
        print("ERROR: Invalid station ID.", file=sys.stderr)
        return 1
    if not COLLECT_SCRIPT.is_file():
        print("ERROR: Collector script is missing.", file=sys.stderr)
        return 1
    db_path = args.db.resolve()
    status_path = args.status_file or db_path.with_suffix(".collector.json")
    command = [
        sys.executable, "-u", "-B", str(COLLECT_SCRIPT),
        "--station-id", args.station_id, "--lookback-hours", str(args.lookback_hours),
        "--db", str(db_path),
    ]
    previous_handler = signal.signal(signal.SIGTERM, _stop)
    try:
        # 잠금은 대기 시간까지 포함한 수집기 전체 수명 동안 유지한다.
        with exclusive_collector_lock(db_path.with_suffix(".collector.lock")):
            return run_loop(
                command, interval_seconds=args.interval_seconds, runs=args.runs,
                child_timeout_seconds=args.child_timeout_seconds,
                max_consecutive_failures=args.max_consecutive_failures,
                status_path=status_path,
            )
    except KeyboardInterrupt:
        print("Collector stopped.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        # 프로세스 종료 또는 직접 함수 호출 테스트 뒤 신호 처리기를 복원한다.
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
