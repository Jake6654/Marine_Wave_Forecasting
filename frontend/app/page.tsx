import { Dashboard } from "../components/dashboard";
import { RefreshButton } from "../components/refresh-button";
import { CollectButton } from "../components/collect-button";
import { getDashboard } from "../lib/dashboard";

// 빌드 시점에 고정하지 않고 요청마다 FastAPI의 저장 자료를 조회한다.
export const dynamic = "force-dynamic";

export default async function Page() {
  const snapshot = await getDashboard();
  return <div className="app-shell">
    <header className="site-header"><a className="brand" href="/" aria-label="Marine Wave Forecasting 홈">
      <svg viewBox="0 0 40 40" aria-hidden="true"><path d="M5 22c5-11 8 11 14 0s9 11 16 0M5 30c5-11 8 11 14 0s9 11 16 0" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" /></svg>
      <span>MARINE<span className="brand-sub">WAVE FORECASTING</span></span></a>
      <nav aria-label="주요 메뉴"><a href="#observations">관측·예측</a><a href="#guide">해석 안내</a></nav>
      <span className="header-label">PUBLIC DATA · EXPERIMENTAL MODEL</span></header>
    <main><div className="page-heading"><div><p className="eyebrow">DEOKJEOKDO <span>·</span> STATION 22101</p><h1>덕적도 관측 대시보드</h1>
      <p className="subtitle">현재 바다를 읽고, 6시간 뒤 한 시점의 파고를 살펴봅니다.</p></div><div className="page-actions"><CollectButton /><RefreshButton /></div></div>
      <Dashboard snapshot={snapshot} /></main>
  </div>;
}
