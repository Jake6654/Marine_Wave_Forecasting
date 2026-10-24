import assert from "node:assert/strict";
import test from "node:test";
import { fetchEndpoint, loadDashboard } from "../lib/api-client";
import { observationSchema, predictionSchema } from "../lib/contracts";
import { observationPayload, predictionPayload } from "./fixtures";

const jsonFetcher = (body: unknown, status = 200): typeof fetch => async () => new Response(JSON.stringify(body), { status });

test("유효한 관측을 파싱하며 캐시를 사용하지 않는다", async () => {
  const fetcher: typeof fetch = async (_url, options) => {
    assert.equal(options?.cache, "no-store");
    assert.equal(options?.redirect, "error");
    assert.ok(options?.signal);
    return new Response(JSON.stringify(observationPayload));
  };
  const result = await fetchEndpoint("http://127.0.0.1:8000", "/observations", observationSchema, fetcher);
  assert.equal(result.ok, true);
});
test("한쪽 HTTP 실패가 다른 카드의 성공을 지우지 않는다", async () => {
  const fetcher: typeof fetch = async (input) => String(input).includes("observations")
    ? new Response(JSON.stringify(observationPayload)) : new Response("PRIVATE", { status: 503 });
  const result = await loadDashboard("http://127.0.0.1:8000", fetcher);
  assert.equal(result.observation.ok, true);
  assert.equal(result.prediction.ok, false);
  assert.ok(!JSON.stringify(result).includes("PRIVATE"));
});
test("API 주소 오류를 화면에 그대로 노출하지 않는다", async () => {
  for (const url of ["broken", "file:///secret", "http://user:SECRET@localhost", "http://localhost/private", "http://localhost/?key=SECRET"]) {
    let called = false;
    const result = await fetchEndpoint(url, "/x", observationSchema, async () => { called = true; throw new Error(); });
    assert.equal(result.ok, false);
    assert.equal(called, false);
    assert.ok(!JSON.stringify(result).includes("SECRET"));
  }
});
test("연결 실패와 timeout을 구분한다", async () => {
  const connection = await fetchEndpoint("http://localhost", "/x", observationSchema, async () => { throw new Error("PRIVATE"); });
  const timeout = await fetchEndpoint("http://localhost", "/x", observationSchema, async () => { throw new DOMException("PRIVATE", "TimeoutError"); });
  assert.ok(!connection.ok && connection.kind === "connection");
  assert.ok(!timeout.ok && timeout.kind === "timeout");
  assert.ok(!JSON.stringify(connection).includes("PRIVATE"));
});
test("잘못된 JSON과 응답 계약을 성공으로 처리하지 않는다", async () => {
  const invalidJson = await fetchEndpoint("http://localhost", "/x", observationSchema, async () => new Response("not JSON"));
  const invalidBody = await fetchEndpoint("http://localhost", "/x", observationSchema, jsonFetcher({ status: "available" }));
  assert.ok(!invalidJson.ok && invalidJson.kind === "invalid");
  assert.ok(!invalidBody.ok && invalidBody.kind === "invalid");
});
test("예측 불가는 통신 실패와 구분해 파싱한다", async () => {
  const result = await fetchEndpoint("http://localhost", "/x", predictionSchema, jsonFetcher({
    ...predictionPayload, status: "unavailable", reason: "source_policy_unverified", wave_reference: null,
  }));
  assert.ok(result.ok && result.data.status === "unavailable");
});
test("손상된 숫자와 잘못된 운영 허용 상태를 거부한다", () => {
  assert.equal(predictionSchema.safeParse({ ...predictionPayload, operational_use_allowed: true }).success, false);
  assert.equal(predictionSchema.safeParse({ ...predictionPayload, predicted_significant_wave_height_m: -1 }).success, false);
  assert.equal(observationSchema.safeParse({ ...observationPayload, observation: null }).success, false);
  assert.equal(predictionSchema.safeParse({ ...predictionPayload, wave_reference: undefined }).success, false);
});
test("파고 구간은 백엔드 값을 보존하며 프론트에서 재분류하지 않는다", async () => {
  const body = { ...predictionPayload, wave_reference: { ...predictionPayload.wave_reference, code: "P3", label: "2~3m 미만" } };
  const result = await fetchEndpoint("http://localhost", "/x", predictionSchema, jsonFetcher(body));
  assert.ok(result.ok && result.data.status === "experimental" && result.data.wave_reference.code === "P3");
});
