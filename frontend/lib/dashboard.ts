import "server-only";
import { loadDashboard } from "./api-client";

export function getDashboard() {
  // 서버 전용 환경변수이며 기상청 인증키나 모델 경로는 프론트에 전달하지 않는다.
  return loadDashboard(process.env.MARINE_API_BASE_URL ?? "http://127.0.0.1:8000");
}
