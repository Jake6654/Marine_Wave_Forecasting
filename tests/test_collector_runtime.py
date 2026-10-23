"""반복 실행, 잠금, 상태 점검을 외부 API 없이 검증한다."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import marine_forecast.collector_runtime as runtime


def fake_process(monkeypatch, results):
    execute = Mock(side_effect=results)
    sleep = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", execute)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    return execute, sleep


def success():
    return subprocess.CompletedProcess(["python"], 0)


def failure():
    return subprocess.CompletedProcess(["python"], 1)


def test_finite_loop_waits_only_between_attempts(monkeypatch, tmp_path):
    execute, sleep = fake_process(monkeypatch, [success(), success()])
    path = tmp_path / "status.json"
    assert runtime.run_loop(["python"], runs=2, status_path=path) == 0
    assert execute.call_count == 2
    assert execute.call_args.kwargs["timeout"] == 60
    sleep.assert_called_once_with(600)
    status = json.loads(path.read_text())
    assert status["state"] == "stopped"
    assert status["attempt"] == 2
    assert status["last_success_at"]
    assert status["pid"] == os.getpid()
    assert not runtime.collector_is_healthy(path)
    assert not list(tmp_path.glob(".status.json.*"))


def test_success_resets_consecutive_failures(monkeypatch, tmp_path):
    execute, _ = fake_process(monkeypatch, [failure(), failure(), success(), failure()])
    path = tmp_path / "status.json"
    assert runtime.run_loop(["python"], runs=4, status_path=path) == 1
    assert execute.call_count == 4
    status = json.loads(path.read_text())
    assert status["consecutive_failures"] == 1
    assert status["last_success_at"] is not None


def test_failure_limit_exits_for_supervisor_restart(monkeypatch, tmp_path):
    execute, sleep = fake_process(monkeypatch, [failure()] * 4)
    path = tmp_path / "status.json"
    assert runtime.run_loop(["python"], status_path=path) == 1
    assert execute.call_count == 3
    assert sleep.call_count == 2
    assert json.loads(path.read_text())["state"] == "fatal"


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired("credential-must-not-be-printed", 60),
    OSError("credential-must-not-be-printed"),
])
def test_process_errors_are_sanitized_and_next_attempt_runs(monkeypatch, capsys, error):
    execute, _ = fake_process(monkeypatch, [error, success()])
    assert runtime.run_loop(["python"], runs=2) == 1
    assert execute.call_count == 2
    output = capsys.readouterr()
    assert "credential-must-not-be-printed" not in output.out + output.err


@pytest.mark.parametrize("changes", [
    {"command": []}, {"command": [123]}, {"interval_seconds": 0},
    {"interval_seconds": float("nan")}, {"interval_seconds": True},
    {"runs": 0}, {"runs": True}, {"child_timeout_seconds": 0},
    {"max_consecutive_failures": 0},
])
def test_invalid_configuration_never_starts_child(monkeypatch, changes):
    execute = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", execute)
    settings = {"command": ["python"], "runs": 1, **changes}
    with pytest.raises((TypeError, ValueError)):
        runtime.run_loop(**settings)
    execute.assert_not_called()


def test_lock_blocks_second_collector_and_releases_after_failure(tmp_path):
    path = tmp_path / "collector.lock"
    with pytest.raises(ValueError):
        with runtime.exclusive_collector_lock(path):
            with pytest.raises(RuntimeError, match="Another collector"):
                with runtime.exclusive_collector_lock(path):
                    pytest.fail("Second lock must not be acquired.")
            raise ValueError("Synthetic failure")
    with runtime.exclusive_collector_lock(path):
        assert path.exists()


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(("changes", "expected"), [
    ({}, True), ({"state": "failure"}, True),
    ({"state": "starting"}, False), ({"state": "fatal"}, False),
    ({"state": "stopped"}, False), ({"pid": -1}, False),
    ({"pid": True}, False), ({"last_success_at": None}, False),
    ({"last_success_at": (NOW - timedelta(hours=2)).isoformat()}, False),
    ({"last_success_at": (NOW + timedelta(seconds=1)).isoformat()}, False),
    ({"last_success_at": "2026-10-24T12:00:00"}, False),
])
def test_health_requires_live_process_and_recent_success(tmp_path, changes, expected):
    path = tmp_path / "status.json"
    status = {
        "state": "waiting", "pid": os.getpid(),
        "last_success_at": NOW.isoformat(), **changes,
    }
    path.write_text(json.dumps(status))
    assert runtime.collector_is_healthy(path, now=NOW) is expected


@pytest.mark.parametrize("body", ["broken", "[]", "{}"])
def test_invalid_status_is_unhealthy(tmp_path, body):
    path = tmp_path / "status.json"
    path.write_text(body)
    assert not runtime.collector_is_healthy(path)
    assert not runtime.collector_is_healthy(tmp_path / "absent.json")


def test_real_child_runs_twice_without_network_and_data_survives(tmp_path):
    db = tmp_path / "counter.sqlite3"
    code = (
        "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute('CREATE TABLE IF NOT EXISTS attempts (id INTEGER)'); "
        "c.execute('INSERT INTO attempts VALUES (1)'); c.commit(); c.close()"
    )
    assert runtime.run_loop(
        [sys.executable, "-B", "-c", code, str(db)],
        interval_seconds=0.01, runs=2,
    ) == 0
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2


def test_real_timeout_terminates_and_reaps_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    status = tmp_path / "status.json"
    code = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    assert runtime.run_loop(
        [sys.executable, "-B", "-c", code, str(pid_file)],
        runs=1, child_timeout_seconds=0.5, max_consecutive_failures=1,
        status_path=status,
    ) == 1
    assert json.loads(status.read_text())["last_exit_code"] == 124
    assert pid_file.is_file()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
