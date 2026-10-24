import { z } from "zod";

// TypeScript 타입만 믿지 않고 외부 HTTP 응답을 실행 시점에도 검사한다.
const timestamp = z.string().datetime({ offset: true });
const nullableValue = z.number().finite().nonnegative().nullable();
const common = {
  station_id: z.literal("22101"),
  generated_at_utc: timestamp,
  quality_status: z.literal("unverified"),
  operational_use_allowed: z.literal(false),
  warnings: z.array(z.string()),
};

const observationValues = z.object({
  observed_at_utc: timestamp,
  observed_at_kst: timestamp,
  significant_wave_height_m: nullableValue,
  wind_speed_mps: nullableValue,
  api_pressure_hpa: z.number().finite().positive().nullable(),
  aqc_raw: z.string(),
  mqc_raw: z.string(),
  is_hourly: z.boolean(),
});

export const observationSchema = z.discriminatedUnion("status", [
  z.object({
    ...common,
    status: z.enum(["available", "stale", "incomplete"]),
    reason: z.string().nullable(),
    observation: observationValues,
    observation_age_minutes: z.number().finite().nonnegative(),
    is_stale: z.boolean(),
    missing_fields: z.array(z.string()),
  }),
  z.object({
    ...common,
    status: z.literal("unavailable"),
    reason: z.string(),
    observation: z.null(),
    observation_age_minutes: z.null(),
    is_stale: z.null(),
    missing_fields: z.array(z.string()),
  }),
]);

const predictionCommon = {
  ...common,
  model_id: z.string(),
  horizon_hours: z.literal(6),
  observed_at_kst: timestamp.nullable().optional(),
  target_at_kst: timestamp.nullable().optional(),
  observation_age_minutes: z.number().finite().nonnegative().nullable().optional(),
};

export const predictionSchema = z.discriminatedUnion("status", [
  z.object({
    ...predictionCommon,
    status: z.literal("experimental"),
    reason: z.null(),
    observed_at_utc: timestamp,
    observed_at_kst: timestamp,
    target_at_utc: timestamp,
    target_at_kst: timestamp,
    observation_age_minutes: z.number().finite().nonnegative(),
    predicted_significant_wave_height_m: z.number().finite().nonnegative(),
    persistence_significant_wave_height_m: z.number().finite().nonnegative(),
    wave_reference: z.object({
      code: z.enum(["P1", "P2", "P3", "P4"]),
      label: z.string(),
      policy_version: z.string(),
    }),
  }),
  z.object({
    ...predictionCommon,
    status: z.literal("unavailable"),
    reason: z.string(),
    wave_reference: z.null(),
    missing_features: z.array(z.string()).optional(),
  }),
]);

export type Observation = z.infer<typeof observationSchema>;
export type Prediction = z.infer<typeof predictionSchema>;

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; kind: "connection" | "timeout" | "http" | "invalid" | "configuration"; message: string; httpStatus?: number };

export interface DashboardSnapshot {
  observation: ApiResult<Observation>;
  prediction: ApiResult<Prediction>;
  requestedAt: string;
}
