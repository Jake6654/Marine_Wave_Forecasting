import assert from "node:assert/strict";
import test from "node:test";
import { forwardCollection } from "../lib/manual-collection";
import { requestCollection } from "../lib/collection-contract";

const token = "synthetic-secret-token-".repeat(3);
const config = { baseUrl: "http://localhost:8000", token };
const payload = { status: "collected", station_id: "22101", received_count: 24, processed_count: 24,
  started_at_utc: "2026-10-24T08:00:00Z", completed_at_utc: "2026-10-24T08:00:01Z",
  latest_observed_at_kst: "2026-10-24T17:00:00+09:00" };

function request(origin = "http://localhost:3000", action = "collect") {
  return new Request("http://localhost:3000/api/observations/collect", {
    method: "POST", headers: { Origin: origin, "X-Marine-Action": action },
  });
}

test("같은 출처 요청만 서버 토큰을 붙여 전달하고 응답에서 비밀값을 제거한다", async () => {
  let calls = 0;
  const response = await forwardCollection(request(), config, async (url, options) => {
    calls++;
    assert.equal(String(url), "http://localhost:8000/v1/stations/22101/observations/collect");
    assert.equal(options?.method, "POST");
    assert.equal(new Headers(options?.headers).get("x-collection-token"), token);
    return Response.json({ ...payload, db_path: "PRIVATE", authKey: "PRIVATE" });
  });
  assert.equal(calls, 1);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.ok(!(await response.text()).includes("PRIVATE"));
});
test("교차 출처·누락 Origin·잘못된 액션은 외부 수집을 호출하지 않는다", async () => {
  for (const input of [request("https://evil.example"), request(""), request("http://localhost:3000", "wrong")]) {
    const response = await forwardCollection(input, config, async () => { throw new Error("Must not fetch"); });
    assert.equal(response.status, 403);
  }
});
test("토큰 설정이 없으면 수집을 비활성화한다", async () => {
  const result = await forwardCollection(request(), { baseUrl: config.baseUrl });
  assert.equal(result.status, 503);
});
test("Next 내부 localhost URL과 브라우저의 127.0.0.1 Host를 구분한다", async () => {
  const input = new Request("http://localhost:3101/api/observations/collect", {
    method: "POST", headers: { Origin: "http://127.0.0.1:3101", Host: "127.0.0.1:3101", "X-Marine-Action": "collect" },
  });
  const response = await forwardCollection(input, config, async () => Response.json(payload));
  assert.equal(response.status, 200);
});
test("중복·cooldown·외부 오류를 안전한 코드로 전달한다", async () => {
  for (const [status, code] of [[409, "collection_busy"], [429, "collection_cooldown"], [502, "collection_upstream_failed"]] as const) {
    const response = await forwardCollection(request(), config, async () => Response.json({ error: { code, message: "PRIVATE" } }, { status }));
    assert.equal(response.status, status);
    const text = await response.text();
    assert.ok(text.includes(code));
    assert.ok(!text.includes("PRIVATE"));
  }
});
test("timeout은 수집 완료를 보장하지 않고 자동 재시도하지 않는다", async () => {
  let calls = 0;
  const response = await forwardCollection(request(), config, async () => {
    calls++;
    throw new DOMException("PRIVATE", "TimeoutError");
  });
  assert.equal(calls, 1);
  assert.match(await response.text(), /collection_timeout/);
});
test("브라우저 요청은 인증키 없이 같은 출처에 POST한다", async () => {
  const result = await requestCollection(async (url, options) => {
    assert.equal(url, "/api/observations/collect");
    assert.equal(options?.method, "POST");
    assert.equal(new Headers(options?.headers).get("x-collection-token"), null);
    return Response.json(payload);
  });
  assert.ok(result.ok && result.data.received_count === 24);
});
test("브라우저는 실패 응답과 손상된 성공 응답을 성공으로 표시하지 않는다", async () => {
  const failed = await requestCollection(async () => Response.json({ error: { code: "collection_cooldown" } }, { status: 429 }));
  assert.ok(!failed.ok && failed.message.includes("30초"));
  const invalid = await requestCollection(async () => Response.json({ status: "collected" }));
  assert.equal(invalid.ok, false);
});
