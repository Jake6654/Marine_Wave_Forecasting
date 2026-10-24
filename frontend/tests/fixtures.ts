import { observationSchema, predictionSchema, type DashboardSnapshot } from "../lib/contracts";

export const observationPayload = {
  status: "available", reason: null, station_id: "22101",
  generated_at_utc: "2026-10-24T00:40:00Z", quality_status: "unverified",
  operational_use_allowed: false, warnings: ["QC unverified"],
  observation_age_minutes: 10, is_stale: false, missing_fields: [],
  observation: {
    observed_at_utc: "2026-10-24T00:30:00Z", observed_at_kst: "2026-10-24T09:30:00+09:00",
    significant_wave_height_m: .7, wind_speed_mps: 2, api_pressure_hpa: 1018,
    aqc_raw: "raw", mqc_raw: "raw", is_hourly: false,
  },
};

export const predictionPayload = {
  status: "experimental", reason: null, station_id: "22101",
  generated_at_utc: "2026-10-24T00:40:00Z", quality_status: "unverified",
  operational_use_allowed: false, warnings: ["Not for navigation"],
  model_id: "rf-test", horizon_hours: 6,
  observed_at_utc: "2026-10-24T00:00:00Z", observed_at_kst: "2026-10-24T09:00:00+09:00",
  target_at_utc: "2026-10-24T06:00:00Z", target_at_kst: "2026-10-24T15:00:00+09:00",
  observation_age_minutes: 40,
  predicted_significant_wave_height_m: .184, persistence_significant_wave_height_m: .5,
  wave_reference: { code: "P1", label: "1m 미만", policy_version: "wave-reference-v1" },
};

export function snapshot(): DashboardSnapshot {
  return {
    observation: { ok: true, data: observationSchema.parse(observationPayload) },
    prediction: { ok: true, data: predictionSchema.parse(predictionPayload) },
    requestedAt: "2026-10-24T00:40:00Z",
  };
}
