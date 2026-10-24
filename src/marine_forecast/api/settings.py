"""API 전용 서버 설정을 환경변수에서 읽고 검증한다."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping


# 소스 실행은 프로젝트 루트, wheel 설치 실행은 서버 작업 디렉터리를 기준으로 한다.
SOURCE_ROOT = Path(__file__).resolve().parents[3]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").is_file() else Path.cwd().resolve()


def _boolean(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "false").strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true/false or 1/0.")
    return value in {"true", "1"}


@dataclass(frozen=True)
class ApiSettings:
    """설정 변경은 요청 인자가 아니라 서버 재시작으로 적용한다."""

    db_path: Path = ROOT / "data/runtime/buoy_observations.sqlite3"
    model_path: Path = ROOT / "artifacts/rf_22101_v1"
    trust_local_model: bool = False
    allow_unverified_source: bool = False
    max_age_minutes: float = 90
    collection_token: str | None = field(default=None, repr=False)
    background_collection: bool = False

    def __post_init__(self):
        if self.collection_token is not None and (
            not isinstance(self.collection_token, str)
            or not self.collection_token.isascii()
            or not self.collection_token.isprintable()
            or len(self.collection_token) < 32
        ):
            raise ValueError("Collection token must contain at least 32 ASCII characters.")
        for value in (self.trust_local_model, self.allow_unverified_source, self.background_collection):
            if type(value) is not bool:
                raise TypeError("API policy settings must be bool.")
        age = self.max_age_minutes
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 < age <= 180:
            raise ValueError("max_age_minutes must be in (0, 180].")
        # 명령을 실행한 디렉터리가 바뀌어도 같은 파일을 사용하게 한다.
        object.__setattr__(self, "db_path", Path(self.db_path).resolve())
        object.__setattr__(self, "model_path", Path(self.model_path).resolve())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ApiSettings":
        source = os.environ if env is None else env
        try:
            age = float(source.get("MARINE_API_MAX_AGE_MINUTES", "90"))
        except ValueError:
            raise ValueError("MARINE_API_MAX_AGE_MINUTES must be numeric.") from None
        return cls(
            db_path=Path(source.get("MARINE_API_DB_PATH", str(ROOT / "data/runtime/buoy_observations.sqlite3"))),
            model_path=Path(source.get("MARINE_API_MODEL_PATH", str(ROOT / "artifacts/rf_22101_v1"))),
            trust_local_model=_boolean(source, "MARINE_API_TRUST_LOCAL_MODEL"),
            allow_unverified_source=_boolean(source, "MARINE_API_ALLOW_UNVERIFIED_SOURCE"),
            max_age_minutes=age,
            collection_token=source.get("MARINE_COLLECTION_TOKEN") or None,
            background_collection=_boolean(source, "MARINE_API_BACKGROUND_COLLECTION"),
        )
