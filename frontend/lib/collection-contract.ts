import { z } from "zod";

export const collectionSchema = z.object({
  status: z.literal("collected"), station_id: z.literal("22101"),
  started_at_utc: z.string().datetime({ offset: true }),
  completed_at_utc: z.string().datetime({ offset: true }),
  received_count: z.number().int().positive(), processed_count: z.number().int().positive(),
  latest_observed_at_kst: z.string().datetime({ offset: true }),
});

export const collectionMessages: Record<string, string> = {
  collection_not_configured: "서버의 수집 토큰 설정이 필요합니다. 실행 안내를 확인해 주세요.",
  collection_forbidden: "서버 간 수집 인증에 실패했습니다. 양쪽 토큰 설정을 확인해 주세요.",
  collection_key_missing: "FastAPI 서버에 기상청 인증키가 설정되지 않았습니다.",
  collection_busy: "다른 수집이 진행 중입니다. 잠시 후 조회를 새로고침해 주세요.",
  collection_cooldown: "수집 요청은 30초 간격으로 가능합니다. 잠시 후 다시 눌러 주세요.",
  collection_upstream_failed: "기상청 자료 조회에 실패했습니다. 잠시 후 다시 시도해 주세요.",
  collection_storage_failed: "관측 DB에 저장하지 못했습니다. 백엔드 저장소를 확인해 주세요.",
  collection_data_invalid: "수집 자료 또는 상태 파일을 확인해야 합니다.",
  collection_timeout: "응답 대기 시간이 초과됐습니다. 수집은 계속될 수 있으니 먼저 새로고침해 주세요.",
  collection_connection_failed: "수집 서버에 연결하지 못했습니다. 백엔드 실행 상태를 확인해 주세요.",
  collection_invalid_response: "수집 응답 형식을 확인하지 못했습니다. 먼저 조회를 새로고침해 주세요.",
  invalid_request: "허용되지 않는 수집 요청입니다.",
};

export type Collection = z.infer<typeof collectionSchema>;
export type CollectionResult = { ok: true; data: Collection } | { ok: false; message: string };

export async function requestCollection(fetcher: typeof fetch = fetch): Promise<CollectionResult> {
  try {
    // 브라우저는 토큰·기상청 키 없이 같은 출처의 Next.js 경로만 호출한다.
    const response = await fetcher("/api/observations/collect", {
      method: "POST", headers: { "X-Marine-Action": "collect" }, signal: AbortSignal.timeout(65000),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      const error = z.object({ error: z.object({ code: z.string() }) }).safeParse(body);
      const code = error.success ? error.data.error.code : "";
      return { ok: false, message: collectionMessages[code] ?? "수집 요청을 완료하지 못했습니다." };
    }
    const parsed = collectionSchema.safeParse(body);
    return parsed.success ? { ok: true, data: parsed.data }
      : { ok: false, message: collectionMessages.collection_invalid_response };
  } catch (error) {
    const timeout = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
    return { ok: false, message: timeout ? collectionMessages.collection_timeout : "수집 응답을 확인하지 못했습니다. 먼저 새로고침해 주세요." };
  }
}
