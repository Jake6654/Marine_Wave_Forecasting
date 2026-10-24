import { collectionMessages, collectionSchema } from "./collection-contract";

interface CollectionConfig { baseUrl: string; token?: string }

function failure(status: number, code: string) {
  return Response.json({ status: "error", error: { code, message: collectionMessages[code] ?? "수집 요청을 완료하지 못했습니다." } },
    { status, headers: { "Cache-Control": "no-store", ...(status === 429 ? { "Retry-After": "30" } : {}) } });
}

export async function forwardCollection(request: Request, config: CollectionConfig, fetcher: typeof fetch = fetch): Promise<Response> {
  // 교차 출처의 폼·스크립트가 사용자 대신 수집을 실행하지 못하게 한다.
  const origin = request.headers.get("origin");
  let sameOrigin = false;
  try {
    const originUrl = new URL(origin ?? "");
    const requestUrl = new URL(request.url);
    // Next.js가 내부 URL을 localhost로 구성해도 브라우저가 보낸 Host로 비교한다.
    const host = request.headers.get("host") ?? requestUrl.host;
    sameOrigin = originUrl.protocol === requestUrl.protocol && originUrl.host === host
      && ["localhost", "127.0.0.1", "[::1]"].includes(originUrl.hostname);
  } catch { /* 누락·잘못된 Origin은 허용하지 않는다. */ }
  if (request.method !== "POST" || !sameOrigin
      || request.headers.get("x-marine-action") !== "collect" || new URL(request.url).search) {
    return failure(403, "invalid_request");
  }
  if (!config.token || config.token.length < 32 || !/^[\x20-\x7E]+$/.test(config.token)) {
    return failure(503, "collection_not_configured");
  }
  let url: URL;
  try {
    const base = new URL(config.baseUrl);
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new Error();
    url = new URL("/v1/stations/22101/observations/collect", base);
  } catch {
    return failure(503, "collection_not_configured");
  }
  try {
    const response = await fetcher(url, { method: "POST", cache: "no-store", redirect: "error",
      headers: { "X-Collection-Token": config.token }, signal: AbortSignal.timeout(60000) });
    if (!response.ok) {
      let code = "collection_upstream_failed";
      try {
        const body = await response.json();
        if (typeof body?.error?.code === "string" && Object.hasOwn(collectionMessages, body.error.code)) code = body.error.code;
      } catch { /* 원문 오류를 브라우저로 반사하지 않는다. */ }
      return failure([403, 404, 409, 429, 502, 503].includes(response.status) ? response.status : 502, code);
    }
    const parsed = collectionSchema.safeParse(await response.json());
    if (!parsed.success) return failure(502, "collection_invalid_response");
    return Response.json(parsed.data, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const timeout = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
    return failure(502, timeout ? "collection_timeout" : "collection_connection_failed");
  }
}
