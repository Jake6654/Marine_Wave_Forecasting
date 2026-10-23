"""SQLite의 최신 관측으로 한 번 예측한다."""

import argparse
import json
import pickle
import sqlite3
import sys
from pathlib import Path

from marine_forecast.data.buoy_store import BuoyObservationStore
from marine_forecast.inference import predict_latest
from marine_forecast.model_artifact import load_model_artifact


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Predict from the latest local buoy observations.")
    parser.add_argument("--db", type=Path, default=ROOT / "data/runtime/buoy_observations.sqlite3")
    parser.add_argument("--model", type=Path, default=ROOT / "artifacts/rf_22101_v1")
    parser.add_argument("--station-id", default="22101")
    parser.add_argument("--max-age-minutes", type=float, default=90)
    parser.add_argument("--trust-local-model", action="store_true",
                        help="Load only a model you created and trust; pickle can execute code.")
    parser.add_argument("--allow-unverified-source", action="store_true",
                        help="Experimental only: acknowledge unresolved QC and pressure semantics.")
    args = parser.parse_args(argv)
    try:
        bundle = load_model_artifact(args.model, trusted=args.trust_local_model)
        result = predict_latest(
            BuoyObservationStore(args.db), bundle, station_id=args.station_id,
            max_age_minutes=args.max_age_minutes,
            allow_unverified_source=args.allow_unverified_source,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError,
            pickle.UnpicklingError, sqlite3.Error):
        # 원문·환경변수·임의 파일 내용을 오류 로그에 노출하지 않는다.
        print(json.dumps({"status": "error", "reason": "invalid_model_or_observation_store"}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "experimental" else 2


if __name__ == "__main__":
    raise SystemExit(main())
