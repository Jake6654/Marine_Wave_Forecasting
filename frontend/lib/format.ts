export function formatValue(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value)
    ? "—" : new Intl.NumberFormat("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

export function formatKst(value: string | null | undefined): string {
  if (!value || Number.isNaN(Date.parse(value))) return "—";
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(value));
  const part = (type: string) => parts.find((item) => item.type === type)?.value;
  return `${part("year")}.${part("month")}.${part("day")} ${part("hour")}:${part("minute")} KST`;
}

export function reasonText(reason: string | null | undefined): string {
  const reasons: Record<string, string> = {
    source_policy_unverified: "QC 정책 미확인으로 예측이 차단됐습니다. 실험 허용 여부는 백엔드 서버 설정에서 관리합니다.",
    no_recent_observations: "최근 24시간 이내 관측 자료가 없습니다.",
    no_hourly_observations: "예측 기준으로 사용할 정시 관측이 없습니다.",
    stale_observation: "관측이 오래되어 최신성 기준을 충족하지 못했습니다.",
    incomplete_latest_features: "최신 관측 또는 정확한 1·3·6시간 전 값이 부족합니다.",
    invalid_model_prediction: "모델이 유효한 파고 예측값을 반환하지 않았습니다.",
    missing_observation_values: "일부 관측 수치가 결측입니다. 결측은 0으로 대체하지 않습니다.",
  };
  return reason ? reasons[reason] ?? "처리 사유가 추가됐습니다. API 응답을 확인해 주세요." : "";
}
