import type { ApiResult, DashboardSnapshot, Observation, Prediction } from "../lib/contracts";
import { formatKst, formatValue, reasonText } from "../lib/format";

function Chip({ text, tone = "neutral" }: { text: string; tone?: "neutral" | "teal" | "amber" | "red" }) {
  return <span className={`chip ${tone}`}>{text}</span>;
}

function Failure({ result }: { result: Extract<ApiResult<unknown>, { ok: false }> }) {
  return <div className="notice error-notice" role="alert"><strong>{result.kind === "invalid" ? "응답 형식 확인 필요" : "조회 실패"}</strong>
    <p>{result.message}</p>{result.httpStatus && <small>HTTP {result.httpStatus}</small>}</div>;
}

function ObservationCard({ result }: { result: ApiResult<Observation> }) {
  const data = result.ok ? result.data : null;
  const observation = data?.observation;
  const state = !data ? "연결 확인 필요" : data.status === "available" ? "수치 조회 가능" : data.status === "stale" ? "오래된 관측" : data.status === "incomplete" ? "일부 결측" : "자료 없음";
  return <section className="panel observation-panel" aria-labelledby="observation-title">
    <div className="panel-top"><div><p className="eyebrow">LATEST OBSERVATION</p><h2 id="observation-title">현재 관측</h2></div>
      <Chip text={state} tone={data?.status === "available" ? "teal" : "amber"} /></div>
    <p className="metric-label">유의파고</p>
    <div className="metric"><span>{formatValue(observation?.significant_wave_height_m)}</span><span className="unit">m</span></div>
    <p className="metric-caption">최근 수신 자료의 관측값 · 미래 예측이 아닙니다</p>
    <div className="mini-metrics"><div><span>풍속 · 센서 1</span><strong>{formatValue(observation?.wind_speed_mps)} <small>m/s</small></strong></div>
      <div><span>API 기압 · PA</span><strong>{formatValue(observation?.api_pressure_hpa)} <small>hPa</small></strong></div></div>
    {!result.ok ? <Failure result={result} /> : data?.reason && <div className="notice"><p>{reasonText(data.reason)}</p></div>}
    {!!data?.missing_fields.length && <p className="detail-text">결측 항목: {data.missing_fields.join(", ")}</p>}
    <dl className="times"><div><dt>관측시각</dt><dd>{formatKst(observation?.observed_at_kst)}</dd></div>
      <div><dt>자료 경과</dt><dd>{data?.observation_age_minutes != null ? `${Math.floor(data.observation_age_minutes)}분` : "—"}</dd></div>
      <div><dt>관측 구분</dt><dd>{observation ? observation.is_hourly ? "정시 관측" : "30분·비정시 관측" : "—"}</dd></div></dl>
  </section>;
}

function PredictionCard({ result }: { result: ApiResult<Prediction> }) {
  const data = result.ok ? result.data : null;
  const predicted = data?.status === "experimental" ? data : null;
  const band = predicted?.wave_reference;
  const bands = [
    ["P1", "1m 미만"], ["P2", "1–2m 미만"], ["P3", "2–3m 미만"], ["P4", "3m 이상"],
  ];
  return <section className="panel prediction-panel" aria-labelledby="prediction-title">
    <div className="panel-top"><div><p className="eyebrow">6-HOUR POINT FORECAST</p><h2 id="prediction-title">6시간 뒤 예측</h2></div>
      <Chip text={predicted ? "실험 예측" : data ? "예측 불가" : "연결 확인 필요"} tone="amber" /></div>
    <p className="metric-label">예측 유의파고</p>
    <div className="metric"><span>{formatValue(predicted?.predicted_significant_wave_height_m, 3)}</span><span className="unit">m</span></div>
    <p className="metric-caption">기준 관측에서 정확히 6시간 뒤 한 시점의 예측</p>
    {!result.ok ? <Failure result={result} /> : data?.status === "unavailable" && <div className="notice"><strong>예측을 표시하지 않습니다</strong><p>{reasonText(data.reason)}</p></div>}
    <div className="reference-head"><span>모의 파고 구간</span><strong>{band ? `${band.code} · ${band.label}` : "등급 없음"}</strong></div>
    <div className="bands" aria-label={band ? `모의 구간 ${band.code}: ${band.label}` : "모의 구간 없음"}>
      {bands.map(([code, label]) => <div key={code} className={band?.code === code ? "band selected" : "band"}>
        <strong>{code}</strong><span>{label}</span>{band?.code === code && <span className="selected-mark" aria-hidden="true">●</span>}</div>)}
    </div>
    <p className="band-caption">구간은 API 결과 그대로 표시합니다. 숫자는 반올림되며, 구간 판정은 원래 예측값 기준입니다.</p>
    <dl className="times"><div><dt>예측 기준시각</dt><dd>{formatKst(data?.observed_at_kst)}</dd></div>
      <div className="target-time"><dt>예측 대상시각</dt><dd>{formatKst(data?.target_at_kst)}</dd></div>
      <div><dt>모델</dt><dd>{data ? "RandomForest · 고정 모델" : "—"}</dd></div></dl>
    {data && <p className="model-id">{data.model_id}</p>}
  </section>;
}

export function Dashboard({ snapshot }: { snapshot: DashboardSnapshot }) {
  const observation = snapshot.observation.ok ? snapshot.observation.data : null;
  const prediction = snapshot.prediction.ok ? snapshot.prediction.data : null;
  return <>
    <div className="warning-banner" role="note"><span className="warning-symbol" aria-hidden="true">!</span><div><strong>실험용 해양기상 참고 정보</strong>
      <p>QC 미확인 자료와 실험 모델을 사용합니다. 공식 예보·특보나 운항 안전 판정이 아닙니다.</p></div><span className="warning-tag">NOT FOR NAVIGATION</span></div>
    <div className="cards" id="observations"><ObservationCard result={snapshot.observation} /><PredictionCard result={snapshot.prediction} /></div>
    <section className="panel status-panel" aria-labelledby="status-title"><div className="status-heading"><p className="eyebrow">DATA TRANSPARENCY</p><h2 id="status-title">데이터 상태</h2></div>
      <div className="status-item"><span>관측 API 응답 생성</span><strong>{formatKst(observation?.generated_at_utc)}</strong></div>
      <div className="status-item"><span>예측 API 응답 생성</span><strong>{formatKst(prediction?.generated_at_utc)}</strong></div>
      <div className="status-item"><span>품질 확인</span><Chip text="QC 미확인" tone="amber" /></div>
    </section>
    <section className="guide" id="guide"><div><p className="eyebrow">READ BEFORE USE</p><h2>숫자보다 먼저, 시각과 상태를 확인하세요.</h2></div>
      <ol><li><span>01</span><div><h3>관측과 예측의 기준은 다를 수 있어요</h3><p>관측은 최신 30분 자료도 표시합니다. 예측은 정시 자료만 사용하므로 두 기준시각을 각각 확인하세요.</p></div></li>
        <li><span>02</span><div><h3>낮은 파고가 안전을 뜻하지 않아요</h3><p>P1~P4는 수치를 나눈 모의 구간입니다. 선박·해역·시정·해류 등을 종합한 운항 위험 등급이 아닙니다.</p></div></li>
        <li><span>03</span><div><h3>결측은 0이 아닌 ‘—’로 표시해요</h3><p>오래되거나 부족한 입력으로는 예측하지 않습니다. 새로고침은 저장 자료만 다시 조회합니다. 새 관측 수집은 기상청의 최근 자료를 가져와 저장하며, 부이에 새로운 측정을 지시하지 않습니다.</p></div></li></ol>
    </section>
    <details className="technical"><summary>기술·처리 정책 확인</summary><dl><div><dt>모델 ID</dt><dd>{prediction?.model_id ?? "—"}</dd></div>
      <div><dt>모의 구간 정책</dt><dd>{prediction?.status === "experimental" ? prediction.wave_reference.policy_version : "—"}</dd></div></dl>
      <p>API PA와 학습 CSV 기압의 수치 일치는 덕적도 2025년 정시 8,043건에 대한 사용자 제공 결과입니다. 다른 기간의 대응과 AQC·MQC 해석은 별도 확인 대상입니다.</p></details>
    <footer><span>MARINE WAVE FORECASTING <span className="footer-dot">/</span> 관측소 한 곳의 실험</span><span>화면 조회 요청 {formatKst(snapshot.requestedAt)}</span></footer>
  </>;
}
