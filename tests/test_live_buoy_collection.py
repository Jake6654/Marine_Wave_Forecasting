"""명시적으로 허용한 경우에만 실제 API와 임시 SQLite를 연결한다."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_BUOY_TESTS") != "1",
    reason="Real KMA API tests require RUN_LIVE_BUOY_TESTS=1 and KMA_API_KEY.",
)


def test_real_api_to_sqlite_and_reload(tmp_path):
    key = os.environ.get("KMA_API_KEY")
    assert key, "KMA_API_KEY must be configured; its value is never printed."
    spec = importlib.util.spec_from_file_location("live_collection", ROOT / "scripts/collect_buoy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path / "live.sqlite3"
    report = module.collect_once(api_key=key, db_path=db, station_id="22101")
    assert report["received_count"] > 0
    assert report["stored_window_count"] == report["received_count"]
    with closing(sqlite3.connect(db)) as connection:
        total, unique_keys = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT station_id || ':' || observed_at) "
            "FROM buoy_observations"
        ).fetchone()
    assert total == unique_keys == report["received_count"]


def test_real_finite_collector_runs_twice_and_preserves_unique_keys(tmp_path):
    assert os.environ.get("KMA_API_KEY"), "KMA_API_KEY is required."
    db = tmp_path / "finite.sqlite3"
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/run_buoy_collector.py"),
         "--db", str(db), "--runs", "2", "--interval-seconds", "1"],
        capture_output=True, text=True, timeout=140, check=False,
    )
    # 실패 시에도 인증키가 포함될 가능성이 있는 원문 출력은 테스트에 붙이지 않는다.
    assert result.returncode == 0, "Live finite collector failed; inspect sanitized application logs."
    assert "[Run 2] Exit=0" in result.stdout
    with closing(sqlite3.connect(db)) as connection:
        total, unique_keys = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT station_id || ':' || observed_at) "
            "FROM buoy_observations"
        ).fetchone()
    assert total > 0 and total == unique_keys
