"""외부 시스템 없이 모의 파고 구간과 예측 불가 처리를 검증한다."""

from dataclasses import FrozenInstanceError

import pytest

from marine_forecast.reference_levels import (
    REFERENCE_POLICY_VERSION,
    classify_wave_reference,
)


@pytest.mark.parametrize(
    "value,expected_code,expected_label",
    [
        (0.0, "P1", "1m 미만"),
        (0.999999999, "P1", "1m 미만"),
        (1.0, "P2", "1~2m 미만"),
        (1.999999999, "P2", "1~2m 미만"),
        (2.0, "P3", "2~3m 미만"),
        (2.999999999, "P3", "2~3m 미만"),
        (3.0, "P4", "3m 이상"),
        (5.0, "P4", "3m 이상"),
    ],
)
def test_classifies_exact_boundaries(
    value,
    expected_code,
    expected_label,
):
    result = classify_wave_reference(
        value,
        prediction_status="experimental",
    )

    assert result is not None
    assert result.code == expected_code
    assert result.label == expected_label
    assert result.policy_version == REFERENCE_POLICY_VERSION


@pytest.mark.parametrize("value", [None, 0.5, float("inf")])
def test_unavailable_prediction_never_gets_a_reference(value):
    result = classify_wave_reference(
        value,
        prediction_status="unavailable",
    )

    assert result is None


def test_missing_prediction_is_not_classified_as_p1():
    result = classify_wave_reference(
        None,
        prediction_status="experimental",
    )

    assert result is None


@pytest.mark.parametrize(
    "value",
    [-1.0, float("nan"), float("inf"), float("-inf")],
)
def test_rejects_invalid_numeric_predictions(value):
    with pytest.raises(ValueError):
        classify_wave_reference(
            value,
            prediction_status="experimental",
        )


@pytest.mark.parametrize("value", [True, False, "1.0", []])
def test_rejects_non_numeric_prediction_types(value):
    with pytest.raises(TypeError):
        classify_wave_reference(
            value,
            prediction_status="experimental",
        )


def test_rejects_unknown_prediction_status():
    with pytest.raises(ValueError, match="Unsupported"):
        classify_wave_reference(
            0.5,
            prediction_status="unknown",
        )


def test_reference_cannot_be_modified():
    result = classify_wave_reference(
        0.5,
        prediction_status="experimental",
    )

    assert result is not None

    with pytest.raises(FrozenInstanceError):
        result.code = "P4"