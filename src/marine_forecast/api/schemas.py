"""HTTP 응답의 구조·수치·시각과 예측 불가 사유를 정의한다."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ValidationIssue(ApiModel):
    location: list[str | int]
    type: str


class ErrorDetail(ApiModel):
    code: str
    message: str
    details: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(ApiModel):
    status: Literal["error"] = "error"
    error: ErrorDetail


class HealthResponse(ApiModel):
    status: Literal["ok", "ready"]


class NormalizationIssue(ApiModel):
    station_id: str
    observed_at: AwareDatetime
    source_field: str
    reason: str


class NormalizationReport(ApiModel):
    policy_version: str
    qc_status: Literal["unverified"]
    pressure_mapping_status: Literal[
        "not_assessed", "within_user_reported_comparison_scope", "outside_user_reported_comparison_scope",
    ]
    pressure_mapping_evidence: dict[str, JsonValue]
    rows: int = Field(ge=0)
    issues: list[NormalizationIssue]
    warnings: list[str]


class ObservationValues(ApiModel):
    observed_at_utc: AwareDatetime
    observed_at_kst: AwareDatetime
    significant_wave_height_m: float | None = Field(ge=0)
    wind_speed_mps: float | None = Field(ge=0)
    api_pressure_hpa: float | None = Field(gt=0)
    aqc_raw: str
    mqc_raw: str
    is_hourly: bool


class ObservationResponse(ApiModel):
    status: Literal["available", "stale", "incomplete", "unavailable"]
    reason: Literal["no_recent_observations", "stale_observation", "missing_observation_values"] | None
    station_id: str
    generated_at_utc: AwareDatetime
    policy_version: str
    quality_status: Literal["unverified"] = "unverified"
    operational_use_allowed: Literal[False] = False
    observation_age_minutes: float | None = Field(default=None, ge=0)
    is_stale: bool | None = None
    missing_fields: list[str] = Field(default_factory=list)
    observation: ObservationValues | None = None
    normalization_report: NormalizationReport | None = None
    warnings: list[str]


class SourceQC(ApiModel):
    observed_at_utc: AwareDatetime
    aqc_raw: str
    mqc_raw: str


class PredictionBase(ApiModel):
    station_id: str
    generated_at_utc: AwareDatetime
    model_id: str
    horizon_hours: Literal[6]
    policy_version: str
    quality_status: Literal["unverified"]
    operational_use_allowed: Literal[False]
    pressure_mapping_evidence: dict[str, JsonValue]
    warnings: list[str]
    normalization_report: NormalizationReport | None = None
    latest_available_observation_at_utc: AwareDatetime | None = None


class ExperimentalPrediction(PredictionBase):
    status: Literal["experimental"]
    reason: None
    observed_at_utc: AwareDatetime
    observed_at_kst: AwareDatetime
    target_at_utc: AwareDatetime
    target_at_kst: AwareDatetime
    observation_age_minutes: float = Field(ge=0)
    predicted_significant_wave_height_m: float = Field(ge=0)
    persistence_significant_wave_height_m: float = Field(ge=0)
    features: dict[str, float]
    source_qc: list[SourceQC]


class UnavailablePrediction(PredictionBase):
    status: Literal["unavailable"]
    reason: Literal[
        "source_policy_unverified", "no_recent_observations", "no_hourly_observations",
        "stale_observation", "incomplete_latest_features", "invalid_model_prediction",
    ]
    observed_at_utc: AwareDatetime | None = None
    observed_at_kst: AwareDatetime | None = None
    target_at_utc: AwareDatetime | None = None
    target_at_kst: AwareDatetime | None = None
    observation_age_minutes: float | None = Field(default=None, ge=0)
    missing_features: list[str] = Field(default_factory=list)


PredictionResponse = Annotated[
    ExperimentalPrediction | UnavailablePrediction, Field(discriminator="status"),
]
