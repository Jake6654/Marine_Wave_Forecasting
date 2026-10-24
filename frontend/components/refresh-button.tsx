"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";

export function RefreshButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button className="refresh-button" disabled={pending} aria-busy={pending}
      onClick={() => startTransition(() => router.refresh())}>
      <span aria-hidden="true" className={pending ? "refresh-icon spinning" : "refresh-icon"}>↻</span>
      {pending ? "조회 중…" : "새로고침"}
    </button>
  );
}
