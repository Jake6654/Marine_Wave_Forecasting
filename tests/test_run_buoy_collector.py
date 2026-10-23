"""실행기 CLI의 설정 연결과 종료 신호를 검증한다."""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from marine_forecast.collector_runtime import exclusive_collector_lock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_buoy_collector.py"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("runner_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_key_does_not_start_loop(runner, monkeypatch, capsys):
    args = runner.parse_args([])
    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.delenv("KMA_API_KEY", raising=False)
    loop = Mock()
    monkeypatch.setattr(runner, "run_loop", loop)
    assert runner.main() == 1
    loop.assert_not_called()
    assert "KMA_API_KEY is not configured" in capsys.readouterr().err


def test_cli_builds_absolute_command_without_key_and_restores_signal(runner, monkeypatch, tmp_path):
    args = runner.parse_args(["--db", str(tmp_path / "live.sqlite3"), "--runs", "2"])
    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.setenv("KMA_API_KEY", "fake-secret-never-on-command-line")
    loop = Mock(return_value=0)
    monkeypatch.setattr(runner, "run_loop", loop)
    previous = signal.getsignal(signal.SIGTERM)
    assert runner.main() == 0
    assert signal.getsignal(signal.SIGTERM) == previous
    command = loop.call_args.args[0]
    assert command[:4] == [sys.executable, "-u", "-B", str(runner.COLLECT_SCRIPT)]
    assert "fake-secret-never-on-command-line" not in " ".join(command)
    assert loop.call_args.kwargs["runs"] == 2


def test_duplicate_cli_does_not_start_second_loop(runner, monkeypatch, tmp_path):
    db = tmp_path / "live.sqlite3"
    args = runner.parse_args(["--db", str(db), "--runs", "1"])
    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.setenv("KMA_API_KEY", "fake")
    loop = Mock()
    monkeypatch.setattr(runner, "run_loop", loop)
    with exclusive_collector_lock(db.with_suffix(".collector.lock")):
        assert runner.main() == 1
    loop.assert_not_called()


def test_real_sigterm_stops_child_and_releases_lock(tmp_path):
    child = tmp_path / "sleeping_collector.py"
    pid_file = tmp_path / "child.pid"
    child.write_text(
        "import os,time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    db = tmp_path / "live.sqlite3"
    code = (
        "import importlib.util,sys; from pathlib import Path; "
        f"s=importlib.util.spec_from_file_location('runner',{str(SCRIPT)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        f"m.COLLECT_SCRIPT=Path({str(child)!r}); "
        f"sys.argv=['runner','--db',{str(db)!r}]; raise SystemExit(m.main())"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code],
        env={**os.environ, "KMA_API_KEY": "fake"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists(), "Synthetic child did not start."
        process.terminate()
        assert process.wait(timeout=5) == 130
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
        with exclusive_collector_lock(db.with_suffix(".collector.lock")):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
