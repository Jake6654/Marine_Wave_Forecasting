"""상주 수집기의 최근 성공과 프로세스 생존을 점검한다."""

import argparse
from pathlib import Path

from marine_forecast.collector_runtime import collector_is_healthy


def main() -> int:
    parser = argparse.ArgumentParser(description="Check buoy collector health.")
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=float, default=2100)
    args = parser.parse_args()
    try:
        healthy = collector_is_healthy(args.status_file, max_age_seconds=args.max_age_seconds)
    except (TypeError, ValueError):
        healthy = False
    print("Collector healthy." if healthy else "Collector unhealthy.")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
