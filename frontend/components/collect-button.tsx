"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { requestCollection } from "../lib/collection-contract";
import { formatKst } from "../lib/format";

export function CollectButton() {
  const router = useRouter();
  const inFlight = useRef(false);
  const [collecting, setCollecting] = useState(false);
  const [refreshing, startTransition] = useTransition();
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  async function collect() {
    // React가 버튼을 다시 그리기 전 발생한 빠른 연속 클릭도 차단한다.
    if (inFlight.current) return;
    inFlight.current = true;
    setCollecting(true);
    setFeedback(null);
    try {
      const result = await requestCollection();
      if (result.ok) {
        setFeedback({ ok: true, text: `${result.data.received_count}건 수집 완료. 최신 관측 ${formatKst(result.data.latest_observed_at_kst)}. 발표 주기에 따라 관측시각이 같을 수 있습니다.` });
        // 저장 성공 후에만 기존 서버 컴포넌트의 관측·예측을 다시 읽는다.
        startTransition(() => router.refresh());
      } else {
        setFeedback({ ok: false, text: result.message });
      }
    } finally {
      inFlight.current = false;
      setCollecting(false);
    }
  }

  const busy = collecting || refreshing;
  return <div className="collection-control">
    <button className="refresh-button collect-button" onClick={collect} disabled={busy} aria-busy={busy}
      title="기상청에 이미 생성된 최근 12시간 자료를 가져와 저장합니다">
      <span aria-hidden="true">↓</span>{collecting ? "관측 수집 중…" : refreshing ? "화면 갱신 중…" : "새 관측 수집"}
    </button>
    {feedback && <p className={`collection-feedback ${feedback.ok ? "success" : "failure"}`} role={feedback.ok ? "status" : "alert"}>{feedback.text}</p>}
  </div>;
}
