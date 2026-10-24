import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { Dashboard } from "../components/dashboard";
import { observationSchema, predictionSchema } from "../lib/contracts";
import { observationPayload, predictionPayload, snapshot } from "./fixtures";

test("관측과 예측 시각·단위·모의 구간을 각각 표시한다", () => {
  const html = renderToStaticMarkup(<Dashboard snapshot={snapshot()} />);
  for (const value of ["0.184", "09:30 KST", "09:00 KST", "15:00 KST", "m/s", "hPa", "QC 미확인", "P1 · 1m 미만", "NOT FOR NAVIGATION"]) assert.ok(html.includes(value), value);
  assert.ok(html.includes('class="band selected"'));
});
test("정책 차단 응답에 숫자·활성 구간을 표시하지 않는다", () => {
  const data = snapshot();
  data.prediction = { ok: true, data: predictionSchema.parse({
    ...predictionPayload, status: "unavailable", reason: "source_policy_unverified", wave_reference: null,
  }) };
  const html = renderToStaticMarkup(<Dashboard snapshot={data} />);
  assert.ok(html.includes("예측이 차단"));
  assert.ok(html.includes("등급 없음"));
  assert.ok(!html.includes("0.184"));
  assert.ok(!html.includes('class="band selected"'));
});
test("오래됨과 결측을 동시에 표시한다", () => {
  const data = snapshot();
  data.observation = { ok: true, data: observationSchema.parse({
    ...observationPayload, status: "stale", reason: "stale_observation", is_stale: true,
    missing_fields: ["wind_speed_mps"], observation: { ...observationPayload.observation, wind_speed_mps: null },
  }) };
  const html = renderToStaticMarkup(<Dashboard snapshot={data} />);
  assert.ok(html.includes("오래된 관측"));
  assert.ok(html.includes("wind_speed_mps"));
  assert.ok(html.includes("—"));
});
test("양쪽 연결 실패에도 안내와 새 조회 시각을 유지한다", () => {
  const data = snapshot();
  data.observation = { ok: false, kind: "connection", message: "관측 연결 실패" };
  data.prediction = { ok: false, kind: "http", message: "모델 사용 불가", httpStatus: 503 };
  const html = renderToStaticMarkup(<Dashboard snapshot={data} />);
  assert.ok(html.includes("관측 연결 실패"));
  assert.ok(html.includes("HTTP 503"));
  assert.ok(html.includes("화면 조회 요청"));
  assert.ok(!html.includes("0.184"));
});
