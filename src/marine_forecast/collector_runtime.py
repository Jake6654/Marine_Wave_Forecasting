"""수집 프로세스의 반복 실행, 중복 실행 방지와 상태 점검을 담당한다."""

import json
import math
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import fcntl


def _positive_number(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number.")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero.")


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")


@contextmanager
def exclusive_collector_lock(path: str | Path):
    """같은 DB를 대상으로 하는 수집기를 한 개만 실행한다(POSIX)."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another collector already holds this DB lock.") from None
        try:
            yield
        finally:
            # 잠금 파일을 지우면 서로 다른 파일에 잠금을 잡는 경합이 생길 수 있다.
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_status(path: Path, status: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        # 독자가 절반만 작성된 JSON을 읽지 않도록 같은 디렉터리에서 교체한다.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(status, temporary, ensure_ascii=False, allow_nan=False)
            temporary.write("\n")
        temporary_path.replace(path)
    finally:
        # 교체나 쓰기가 실패해도 이번 실행의 임시 파일만 정리한다.
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run_loop(
    command: list[str], *, interval_seconds: float = 600,
    runs: int | None = None, child_timeout_seconds: float = 60,
    max_consecutive_failures: int = 3, status_path: Path | None = None,
) -> int:
    """순차 수집을 반복하고 연속 실패 한계에 도달하면 종료한다."""
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("command must be a non-empty list of strings.")
    _positive_number(interval_seconds, "interval_seconds")
    _positive_number(child_timeout_seconds, "child_timeout_seconds")
    _positive_integer(max_consecutive_failures, "max_consecutive_failures")
    if runs is not None:
        _positive_integer(runs, "runs")
    if status_path is not None:
        status_path = Path(status_path)

    status = {
        "pid": os.getpid(), "state": "starting", "attempt": 0,
        "last_success_at": None, "consecutive_failures": 0,
        "last_exit_code": None, "elapsed_seconds": None,
    }

    def publish():
        if status_path is not None:
            _write_status(status_path, status)

    had_failure = False
    publish()
    try:
        while runs is None or status["attempt"] < runs:
            status["attempt"] += 1
            status["state"] = "running"
            publish()
            # UTC는 로그의 실제 시각, monotonic은 실행시간 측정에 사용한다.
            started = time.monotonic()
            print(
                f"[Run {status['attempt']}] Started UTC: "
                f"{datetime.now(timezone.utc).isoformat()}", flush=True,
            )
            try:
                # 현재 자식 프로세스가 끝나기 전에는 다음 실행을 시작하지 않는다.
                completed = subprocess.run(
                    command, stdin=subprocess.DEVNULL,
                    timeout=child_timeout_seconds, check=False,
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                # run()이 자식을 종료하고 회수한 후 이 예외를 전달한다.
                exit_code = 124
                print("Collector timed out; child terminated.", flush=True)
            except OSError:
                # 원래 예외나 환경변수를 출력하지 않아 인증 정보 노출을 막는다.
                exit_code = 127
                print("Could not start collector process.", flush=True)

            status["elapsed_seconds"] = time.monotonic() - started
            status["last_exit_code"] = exit_code
            if exit_code == 0:
                status["last_success_at"] = datetime.now(timezone.utc).isoformat()
                status["consecutive_failures"] = 0
                status["state"] = "success"
            else:
                had_failure = True
                status["consecutive_failures"] += 1
                status["state"] = "failure"
            print(
                f"[Run {status['attempt']}] Exit={exit_code}; "
                f"elapsed={status['elapsed_seconds']:.2f}s; "
                f"consecutive_failures={status['consecutive_failures']}", flush=True,
            )

            if status["consecutive_failures"] >= max_consecutive_failures:
                # 컨테이너 관리자가 재시작할 수 있도록 실패 상태로 종료한다.
                status["state"] = "fatal"
                publish()
                return 1
            publish()
            if runs is not None and status["attempt"] >= runs:
                break
            status["state"] = "waiting"
            publish()
            # 즉시 재시도하지 않고 완료 시점부터 지정한 간격을 기다린다.
            time.sleep(interval_seconds)
        return 1 if had_failure else 0
    finally:
        # 종료된 수집기의 오래된 상태를 정상 운영으로 오인하지 않게 한다.
        if status["state"] != "fatal":
            status["state"] = "stopped"
        publish()


def collector_is_healthy(
    path: str | Path, *, max_age_seconds: float = 2100,
    now: datetime | None = None,
) -> bool:
    """프로세스 생존과 최근 수집 성공을 검사한다. 관측 품질 검사는 아니다."""
    _positive_number(max_age_seconds, "max_age_seconds")
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone.")
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(status, dict) or status.get("state") not in {
            "running", "waiting", "success", "failure",
        }:
            return False
        pid = status.get("pid")
        if type(pid) is not int or pid <= 0:
            return False
        succeeded = datetime.fromisoformat(status["last_success_at"])
        if succeeded.tzinfo is None or succeeded.utcoffset() is None:
            return False
        age = (current - succeeded).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return False
        os.kill(pid, 0)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True
