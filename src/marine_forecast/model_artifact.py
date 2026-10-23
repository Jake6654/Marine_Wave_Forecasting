"""학습 모델과 입력 계약을 한 디렉터리에 저장하고 검증한다."""

import hashlib
import json
import pickle
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.utils.validation import check_is_fitted

from marine_forecast.features import FEATURE_COLUMNS, HORIZON_HOURS


@dataclass(frozen=True)
class ModelBundle:
    """학습된 모델과 이를 해석하는 데 필요한 출처 정보를 함께 전달한다."""

    model: RandomForestRegressor
    metadata: dict[str, object]


def _validate_model(model: RandomForestRegressor) -> None:
    if not isinstance(model, RandomForestRegressor):
        raise TypeError("Expected RandomForestRegressor.")
    check_is_fitted(model)
    if list(model.feature_names_in_) != list(FEATURE_COLUMNS):
        raise ValueError("Model feature order does not match the project contract.")
    if model.n_outputs_ != 1:
        raise ValueError("Expected a single wave-height prediction target.")


def save_model_artifact(
    model: RandomForestRegressor, path: str | Path, *, provenance: dict[str, object],
) -> dict[str, object]:
    """기존 산출물을 덮어쓰지 않고 모델과 메타데이터를 함께 저장한다."""
    _validate_model(model)
    destination = Path(path).resolve()
    if destination.exists():
        raise FileExistsError("Model artifact already exists; choose a new version directory.")
    if provenance.get("station_id") != "22101":
        raise ValueError("This model artifact supports station 22101 only.")
    payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
    digest = hashlib.sha256(payload).hexdigest()
    metadata = {
        "schema_version": 1, "model_id": f"rf-22101-{digest[:16]}",
        "model_sha256": digest, "feature_columns": list(FEATURE_COLUMNS),
        "horizon_hours": HORIZON_HOURS, "station_id": "22101",
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__, "pandas_version": pd.__version__,
        "model_parameters": model.get_params(), "provenance": provenance,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 중간 실패로 불완전한 산출물이 최종 경로에 노출되지 않게 한다.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".model-") as temporary:
        staging = Path(temporary) / "artifact"
        staging.mkdir()
        (staging / "model.pkl").write_bytes(payload)
        (staging / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        staging.rename(destination)
    return metadata


def load_model_artifact(path: str | Path, *, trusted: bool = False) -> ModelBundle:
    """신뢰한 로컬 산출물만 역직렬화한다. 해시는 서명이나 신뢰 검증이 아니다."""
    # pickle은 로딩 중 코드가 실행될 수 있으므로 외부 파일을 자동 신뢰하지 않는다.
    if trusted is not True:
        raise ValueError("Only load a trusted local model artifact; pickle can execute code.")
    directory = Path(path).resolve()
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("Unsupported model metadata.")
    if (
        metadata.get("feature_columns") != list(FEATURE_COLUMNS)
        or metadata.get("horizon_hours") != HORIZON_HOURS
        or metadata.get("station_id") != "22101"
    ):
        raise ValueError("Incompatible model contract.")
    for name, actual in (
        ("python_version", platform.python_version()),
        ("sklearn_version", sklearn.__version__), ("pandas_version", pd.__version__),
    ):
        if metadata.get(name) != actual:
            raise ValueError(f"Model environment mismatch: {name}.")
    payload = (directory / "model.pkl").read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != metadata.get("model_sha256"):
        raise ValueError("Model checksum mismatch.")
    if metadata.get("model_id") != f"rf-22101-{digest[:16]}":
        raise ValueError("Model ID mismatch.")
    model = pickle.loads(payload)
    _validate_model(model)
    if model.get_params() != metadata.get("model_parameters"):
        raise ValueError("Model parameters do not match metadata.")
    return ModelBundle(model=model, metadata=metadata)
