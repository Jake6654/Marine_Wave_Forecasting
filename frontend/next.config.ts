import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  // 실행 중인 개발 서버와 검증용 빌드의 파일 충돌을 피한다.
  distDir: process.env.MARINE_NEXT_DIST_DIR ?? ".next",
};

export default config;
