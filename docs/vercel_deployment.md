# Vercel 프론트엔드 배포

작성일: 2026-10-24.

운영 주소: https://marine-wave-forecasting.vercel.app

## 검증 결과

- Vercel 배포 ID: `dpl_3gGpVjZ1KKACjmG9FGQK92kdijJA`
- Production 배포 상태: `READY`
- Vercel 원격 의존성 설치·Next.js 빌드·TypeScript 검사 통과
- 실제 운영 주소에서 대시보드 렌더링 확인
- Render 연결 전이므로 관측·예측은 연결 확인 필요 상태임을 확인
- 기상청 수집·추론의 배포 환경 end-to-end 검증은 아직 수행하지 않음

기능 로직은 변경하지 않았으며 이번 작업에서 단위 테스트를 다시 실행하지 않았다.

## 배포 범위

- Vercel 계정/팀: `jae-hyuks-projects`
- 프로젝트: `marine-wave-forecasting` (화면 이름: Marine Wave Forecasting)
- 배포 루트: `frontend/`
- 프레임워크: Next.js
- FastAPI는 별도로 Render에 배포할 예정이며 이번 작업에 포함하지 않는다.

로컬 `.env*`, `.next*`, `node_modules`, 캐시 파일은 `.vercelignore`로 업로드에서
제외했다. 기존 기상청 인증키나 수집 토큰을 Vercel에 등록하지 않았다.
로컬 프로젝트 연결 정보인 `.vercel/`은 Git에서 제외한다.

## 배포 명령

```bash
cd frontend
npx --yes vercel@latest deploy --prod --yes
```

기존 전역 CLI 44.2.7은 서버의 최소 버전 요구로 거절돼 최신 공식 CLI를
사용했다. 전역 CLI나 프로젝트 의존성은 변경하지 않았다.

## Render 백엔드가 준비되면

1. Render의 FastAPI HTTPS 주소와 관측·예측 조회 API 동작을 확인한다.
2. Vercel 프로젝트 Settings → Environment Variables에서
   `MARINE_API_BASE_URL`을 Render의 origin으로 설정한다.
   예: `https://서비스이름.onrender.com` (경로 없이 지정).
3. Production 환경에 적용하고 Vercel을 다시 배포한다.
4. 실제 관측시각·예측 기준시각·오류 상태를 확인한다.

현재 기본값은 `http://127.0.0.1:8000`이다. Vercel의 localhost는 사용자 Mac이
아니므로, Render 주소 연결 전 화면은 API 연결 실패 상태를 표시한다.
임의의 관측·예측값이나 가짜 성공 상태를 넣지 않았다.
Next.js 서버가 FastAPI를 호출하는 구조라 단순 조회에 브라우저 CORS 허용을
무조건 추가할 필요는 없다.

## 수동 수집 버튼은 별도 작업 필요

현재 구현은 로컬 Origin만 허용하며 기본적으로 서버 토큰이 없으면 차단된다.
따라서 공개 Vercel에서 버튼을 사용하려면 아래를 **별도 구현·검증**해야 한다.

- 정확한 Vercel origin 허용과 프록시 환경의 HTTPS/Host 검증
- 로그인·수집 실행 권한 및 사용자별 호출 제한
- Vercel과 Render 사이의 동일한 `MARINE_COLLECTION_TOKEN` 설정
- `KMA_API_KEY`는 Render에만 설정
- Render 저장소의 지속성 및 다중 인스턴스 잠금 정책

공유 서버 토큰만 추가하거나 로컬 Origin 검사를 제거하는 것으로
공개 사용자 인증이 완성되지는 않는다. 현재의 재호출 제한·SQLite 파일 잠금도
서로 다른 서버 인스턴스 간 분산 잠금은 아니다.
Render 무료 인스턴스의 시작 지연이나 처리 시간은 프론트의 조회 제한 시간과
맞춰 실환경에서 확인해야 한다.
