"""가짜 인증키로 Compose 구문과 영구 저장·재시작 설정을 검증한다."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_compose_configuration_keeps_data_persistent_and_key_out_of_command():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable; Compose validation not performed.")
    # 실제 .env의 키가 Compose 출력에 포함되지 않게 환경변수로 대체한다.
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.collector.yaml"),
         "config", "--format", "json"],
        env={**os.environ, "KMA_API_KEY": "synthetic-test-key"},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, "Compose configuration validation failed."
    service = json.loads(result.stdout)["services"]["collector"]
    assert service["restart"] == "unless-stopped"
    assert service["init"] is True
    assert service["read_only"] is True
    assert service["environment"]["KMA_API_KEY"] == "synthetic-test-key"
    assert "synthetic-test-key" not in " ".join(service["command"])
    assert service["volumes"][0]["type"] == "volume"
    assert service["volumes"][0]["target"] == "/var/lib/marine-forecast"
    assert "scripts/check_buoy_collector.py" in service["healthcheck"]["test"]


def test_build_and_git_rules_exclude_live_data_and_credentials():
    dockerfile = (ROOT / "Dockerfile.collector").read_text()
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "COPY . " not in dockerfile
    assert "COPY .env" not in dockerfile
    assert "USER collector" in dockerfile
    assert "**" in ignore
    assert not any(line.startswith("!.env") or line.startswith("!data") for line in ignore)
    assert "/data/runtime/" in (ROOT / ".gitignore").read_text().splitlines()
