"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="loading-screen"><h1>화면을 불러오지 못했습니다</h1><p>예측 수치를 대신 표시하지 않습니다. 잠시 후 다시 시도해 주세요.</p><button className="refresh-button" onClick={reset}>다시 시도</button></main>;
}
