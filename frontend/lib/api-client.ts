import type { z } from "zod";
import { observationSchema, predictionSchema, type ApiResult, type DashboardSnapshot } from "./contracts";

type Fetcher = typeof fetch;

export async function fetchEndpoint<T>(
  baseUrl: string, path: string, schema: z.ZodType<T>, fetcher: Fetcher = fetch,
): Promise<ApiResult<T>> {
  let url: URL;
  try {
    const base = new URL(baseUrl);
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
      throw new Error("Invalid API origin");
    }
    url = new URL(path, base);
  } catch {
    return { ok: false, kind: "configuration", message: "서버의 API 주소 설정을 확인해 주세요." };
  }
  try {
    // 오래된 응답 재사용과 무제한 대기를 막는다. 자동 재시도는 하지 않는다.
    const response = await fetcher(url, { cache: "no-store", signal: AbortSignal.timeout(5000), redirect: "error" });
    if (!response.ok) {
      return { ok: false, kind: "http", httpStatus: response.status,
        message: response.status === 503 ? "API가 모델 또는 관측 저장소를 사용할 수 없습니다." : "API 요청을 처리하지 못했습니다." };
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return { ok: false, kind: "invalid", message: "API가 올바른 JSON 응답을 보내지 않았습니다." };
    }
    const parsed = schema.safeParse(body);
    if (!parsed.success) {
      return { ok: false, kind: "invalid", message: "API 응답 형식이 맞지 않습니다. 백엔드 버전을 확인해 주세요." };
    }
    return { ok: true, data: parsed.data };
  } catch (error) {
    // 백엔드 주소·예외 원문을 화면에 노출하지 않는다.
    const timedOut = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
    return { ok: false, kind: timedOut ? "timeout" : "connection",
      message: timedOut ? "API 응답 대기 시간이 초과됐습니다." : "API에 연결할 수 없습니다. 백엔드 실행 상태를 확인해 주세요." };
  }
}

export async function loadDashboard(baseUrl: string, fetcher: Fetcher = fetch): Promise<DashboardSnapshot> {
  const requestedAt = new Date().toISOString();
  // 한쪽 조회가 실패해도 다른 카드의 결과는 그대로 제공한다.
  const [observation, prediction] = await Promise.all([
    fetchEndpoint(baseUrl, "/v1/stations/22101/observations/latest", observationSchema, fetcher),
    fetchEndpoint(baseUrl, "/v1/stations/22101/predictions/latest", predictionSchema, fetcher),
  ]);
  return { observation, prediction, requestedAt };
}
