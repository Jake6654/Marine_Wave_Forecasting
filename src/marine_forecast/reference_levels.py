"""예측 유의파고를 운항 안전 판정과 구분된 모의 구간으로 분류한다."""

import math
from dataclasses import dataclass
from typing import Literal


REFERENCE_POLICY_VERSION = "wave-reference-v1"

@dataclass(frozen=True)
class WaveReference:
    """분류 코드와 수치 구간 표시 문구를 보관한다"""

    code: Literal["P1", "P2", "P3", "P4"]
    label: str
    policy_version: str = REFERENCE_POLICY_VERSION

def classify_wave_reference(
        predicted_wave_height_m: float | None,
        *,
        prediction_status: Literal["experiemntal", "unavailiable"],
) -> WaveReference | None:
    """실험이 예측에 성공한 경우에만 모의 파고 구간을 반환한다"""

    # 알 수 없는 상태를 예측 성공으로 해석하지 않는다.
    if prediction_status not in ("experimental", "unavailable"):
        raise ValueError("Unsupported prediction status.")

    # 오래된 값이나 남아 있는 숫자로 예측 불가 상태를 덮어쓰지 않는다.
    if prediction_status == "unavailable":
        return None

    
    if predicted_wave_height_m is None:
        return None

    if (
        isinstance(predicted_wave_height_m, bool)
        or not isinstance(predicted_wave_height_m, (int, float))
    ):
        raise TypeError("Predicted wave height must be a real number.")

    value = float(predicted_wave_height_m)

    if not math.isfinite(value) or value < 0:
        raise ValueError(
            "Predicted wave height must be finite and non-negative."
        )

    # 표시를 위한 반올림이 구간 판정을 바꾸지 않도록 원래 수치를 사용한다.
    if value < 1.0:
        return WaveReference(code="P1", label="1m 미만")

    if value < 2.0:
        return WaveReference(code="P2", label="1~2m 미만")

    if value < 3.0:
        return WaveReference(code="P3", label="2~3m 미만")

    return WaveReference(code="P4", label="3m 이상")
