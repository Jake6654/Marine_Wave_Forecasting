"""외부 서비스 없이 서버 설정의 기본 차단과 입력 검증을 검사한다."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from marine_forecast.api.schemas import ObservationValues
from marine_forecast.api.settings import ApiSettings


def test_default_api_settings_do_not_trust_models_or_enable_experimental_predictions():
    settings = ApiSettings.from_env({})
    assert settings.trust_local_model is False
    assert settings.allow_unverified_source is False
    assert settings.db_path.is_absolute()
    assert settings.max_age_minutes == 90


def test_environment_configuration_is_explicit(tmp_path):
    settings = ApiSettings.from_env({
        "MARINE_API_DB_PATH": str(tmp_path / "a.sqlite3"),
        "MARINE_API_MODEL_PATH": str(tmp_path / "model"),
        "MARINE_API_TRUST_LOCAL_MODEL": "true",
        "MARINE_API_ALLOW_UNVERIFIED_SOURCE": "1",
        "MARINE_API_MAX_AGE_MINUTES": "120",
    })
    assert settings.db_path == tmp_path / "a.sqlite3"
    assert settings.trust_local_model and settings.allow_unverified_source
    assert settings.max_age_minutes == 120


@pytest.mark.parametrize("name", ["MARINE_API_TRUST_LOCAL_MODEL", "MARINE_API_ALLOW_UNVERIFIED_SOURCE"])
@pytest.mark.parametrize("value", ["yes", "", "enabled", "tru"])
def test_misspelled_security_settings_do_not_silently_enable_features(name, value):
    with pytest.raises(ValueError):
        ApiSettings.from_env({name: value})


@pytest.mark.parametrize("value", ["0", "-1", "181", "nan", "inf", "invalid"])
def test_invalid_maximum_age_is_rejected(value):
    with pytest.raises(ValueError):
        ApiSettings.from_env({"MARINE_API_MAX_AGE_MINUTES": value})


@pytest.mark.parametrize("value", [True, False, 0, float("nan"), 181])
def test_direct_configuration_also_checks_age(value):
    with pytest.raises(ValueError):
        ApiSettings(max_age_minutes=value)


def test_direct_configuration_requires_actual_bools():
    with pytest.raises(TypeError):
        ApiSettings(trust_local_model="false")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_observation_response_rejects_non_finite_or_negative_values(value):
    with pytest.raises(ValidationError):
        ObservationValues(
            observed_at_utc="2026-10-24T00:00:00Z", observed_at_kst="2026-10-24T09:00:00+09:00",
            significant_wave_height_m=value, wind_speed_mps=1, api_pressure_hpa=1018,
            aqc_raw="raw", mqc_raw="raw", is_hourly=True,
        )
