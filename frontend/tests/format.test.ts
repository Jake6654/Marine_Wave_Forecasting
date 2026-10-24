import assert from "node:assert/strict";
import test from "node:test";
import { formatKst, formatValue, reasonText } from "../lib/format";

test("결측·비유한 수치를 0으로 바꾸지 않는다", () => {
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(formatValue(value), "—");
  assert.equal(formatValue(0), "0.0");
  assert.equal(formatValue(.184, 3), "0.184");
});
test("서버 로컬 시간대와 무관하게 KST로 표시한다", () => {
  assert.equal(formatKst("2026-10-23T20:00:00Z"), "2026.10.24 05:00 KST");
  assert.equal(formatKst("2026-10-24T05:00:00+09:00"), "2026.10.24 05:00 KST");
  assert.equal(formatKst(null), "—");
  assert.equal(formatKst("broken"), "—");
});
test("알 수 없는 사유는 성공이나 안전으로 해석하지 않는다", () => {
  assert.match(reasonText("stale_observation"), /오래/);
  assert.match(reasonText("new_reason"), /확인/);
  assert.equal(reasonText(null), "");
});
