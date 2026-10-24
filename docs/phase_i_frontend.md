# Phase I1: Next.js 최소 대시보드

작성일: 2026-10-24. 범위는 기존 FastAPI를 읽는 로컬 프론트엔드다.
Sites 스킬의 로컬 작업 경로와 검증 지침을 적용했고, 사용자 지정 프레임워크인
Next.js를 사용했다. Sites 등록·외부 호스팅·배포는 수행하지 않았다.

후속 변경: 기존 읽기 전용 새로고침과 별도로 **새 관측 수집** 버튼을 추가했다.
아래 최초 구현 범위와 구분하며, 최신 설정·동작·검증은 [수동 관측 수집 안내](manual_collection.md)를 참고한다.

## 1. 목표와 제외 범위

```text
기상청 → 별도 Python 수집기 → SQLite
→ FastAPI: 관측·추론·모의 구간
→ Next.js 서버: HTTP 조회·응답 계약 검사
→ 브라우저: 관측·예측·시각·상태 표시
```

단일 페이지에 덕적도 22101의 현재 관측, 6시간 뒤 파고, 모의 구간,
관측/예측의 기준·목표 시각, 결측·지연·연결 실패·QC 경고를 표시한다.

프론트는 관측을 직접 수집하거나 SQLite·pickle에 접근하지 않는다.
모델 예측·P1~P4 분류·QC 정책을 재구현하지 않는다.
로그인·지도·여러 관측소·차트·자동 갱신·외부 배포는 이번 범위에서 제외한다.

## 2. 파일과 책임

| 파일 | 책임 |
|---|---|
| `frontend/package.json`, `package-lock.json` | npm 명령·의존성과 설치 버전 고정 |
| `frontend/app/layout.tsx` | 한국어 문서·메타데이터·공통 CSS |
| `frontend/app/page.tsx` | 서버에서 데이터 조회 후 한 페이지 조립 |
| `frontend/app/loading.tsx` | 서버 조회 대기 화면 |
| `frontend/app/error.tsx` | 예상하지 못한 화면 오류·재시도 |
| `frontend/app/globals.css` | 색상·카드·상태·반응형 배치 |
| `frontend/components/dashboard.tsx` | 검증된 데이터를 받아 관측·예측·안내 표시 |
| `frontend/components/refresh-button.tsx` | 브라우저 새로고침 상호작용만 담당 |
| `frontend/lib/contracts.ts` | Zod 런타임 응답 계약·TypeScript 타입 |
| `frontend/lib/api-client.ts` | HTTP·timeout·JSON/응답 검사·부분 실패 처리 |
| `frontend/lib/dashboard.ts` | 서버 전용 API 주소 설정·조회 진입점 |
| `frontend/lib/format.ts` | null·숫자·KST·사유 문구 표현 |
| `frontend/tests/` | 합성 응답·fetch 대체·컴포넌트 정적 렌더 테스트 |
| `frontend/.env.example` | 서버 전용 API origin 예시 |
| `frontend/.gitignore` | node_modules·빌드·환경변수·캐시 제외 |

기존 Python 코드와 사용자가 작성 중인 테스트는 수정하지 않았다.
프론트엔드를 별도 디렉터리로 분리해 Python 패키지와 Node 의존성을 섞지 않는다.

## 3. 서버와 클라이언트의 구분

`page.tsx`는 async 서버 컴포넌트다. 브라우저가 아니라 Next.js 서버에서
FastAPI를 호출하므로 FastAPI CORS 설정을 추가할 필요가 없다.
브라우저에서 FastAPI를 직접 조회하는 구조로 바꾸면 CORS를 다시 고려해야 한다.

`dynamic = "force-dynamic"`과 fetch의 `cache: "no-store"`로 빌드 때의 관측을
고정 표시하지 않는다. 새 화면 요청과 수동 새로고침 때 저장 자료를 다시 조회한다.
조회 후 화면을 계속 열어두면 시간이 자동 갱신되지 않으며 표시 상태는 조회 시점 기준이다.

`refresh-button.tsx`만 `"use client"`로 선언한다.
`useTransition()`의 pending 상태 동안 버튼을 비활성화하고,
`router.refresh()`로 서버 컴포넌트의 데이터를 다시 요청한다.
이 동작은 기상청의 새 관측 수집 명령이 아니다.

`dashboard.ts`의 `import "server-only"`는 서버용 API 주소 설정이
실수로 클라이언트 컴포넌트에서 import되는 것을 방지한다.
`MARINE_API_BASE_URL`에 `NEXT_PUBLIC_` 접두사를 붙이지 않는다.
기상청 인증키와 모델 파일은 프론트 환경변수에 넣지 않는다.

## 4. 조회 흐름과 실패 처리

```text
getDashboard()
→ 서버 환경변수의 API origin 확인
→ 관측·예측 경로를 Promise.all로 병렬 조회
→ HTTP 상태 확인
→ JSON 파싱
→ Zod 응답 계약 검사
→ 각 카드에 성공 또는 오류 결과 전달
```

`ApiResult<T>`는 두 경우를 구분한다.

```typescript
{ ok: true, data: 검증된응답 }
{ ok: false, kind: 오류종류, message: 화면문구 }
```

각 요청이 실패를 결과 객체로 반환하므로 예측이 503이어도 관측 카드가 사라지지 않는다.
timeout은 5초이며 자동 재시도·자동 polling은 없다. 기본 fetch는 리다이렉트를
따라가지 않는다. 설정은 http/https origin만 허용하고 URL 안의 인증정보·쿼리·경로는 거부한다.
리버스 프록시의 `/api` 같은 하위 경로를 사용하는 배포 구성은 현재 지원하지 않는다.

| 경우 | 표시 |
|---|---|
| 네트워크 연결 실패 | 백엔드 실행 확인 안내 |
| 응답 timeout | 대기 시간 초과 |
| HTTP 503 | 모델 또는 저장소 사용 불가 안내 |
| JSON·응답 구조 불일치 | API 버전·응답 형식 확인 안내 |
| HTTP 200 + unavailable | 예측 불가 사유 표시, 예측 숫자·선택 구간 없음 |
| 관측 stale | 기존 관측값은 표시하되 오래됨·경과시간 표시 |
| 관측 incomplete | null을 `—`로 표시하고 결측 항목 표시 |

TypeScript는 HTTP 응답을 실행 시점에 검증하지 않는다.
따라서 `as Prediction`으로 강제 변환하지 않고 Zod `safeParse()`를 사용한다.
NaN·음수·누락된 모의 구간·운영 허용 true 같은 잘못된 응답은 정상 수치로 표시하지 않는다.
API 추가 메타데이터는 필요한 표시 필드만 파싱하며, 모든 원문을 브라우저에 전달하지 않는다.
오류 원문·백엔드 주소·HTTP 오류 본문은 화면에 반사하지 않는다.

## 5. 관측과 예측을 다르게 표시하는 이유

예를 들어 관측 API의 최신 값이 09:30이고 예측 기준은 09:00이라면:

```text
현재 관측 카드: 09:30 값
예측 카드: 09:00 → 15:00 예측
```

예측 기준을 화면 조회 시각이나 최신 30분 관측시각으로 바꾸지 않는다.
모든 시간은 `Intl.DateTimeFormat`의 `Asia/Seoul`로 표시하여 호스트 시간대에
따라 달라지지 않게 한다. 관측시각, API 응답 생성시각, 화면 조회 요청시각을 구분한다.

숫자 0은 그대로 0.0으로 표시하고 null·undefined·비유한 값은 `—`다.
관측 파고는 소수 1자리, 예측 파고는 소수 3자리로 표시한다.
표시 반올림이 구간을 바꾸지 않게 모의 구간은 API 값을 그대로 사용한다.
경계 근처에서 표시 숫자가 반올림될 수 있다는 설명을 함께 둔다.

구간 범위 설명은 고정 범례이고 활성 코드는 API의 `wave_reference.code`다.
프론트는 예측 수치에서 코드를 다시 계산하지 않는다.
P1의 색상도 안전 신호가 아니며 ‘안전·출항 가능’이라는 문구는 사용하지 않는다.

## 6. 설치·실행

이 프로젝트의 Node 기준은 22 이상이다. 프론트에는 별도의 npm 잠금 파일을 둔다.

프로젝트 루트의 첫 번째 터미널에서 기존 FastAPI를 실행한다.
아래 true 설정은 본인이 만든 모델을 신뢰하고, 미확인 QC를 받아들이는 시연용 옵션이다.

```bash
export MARINE_API_TRUST_LOCAL_MODEL=true
export MARINE_API_ALLOW_UNVERIFIED_SOURCE=true
uv run --locked uvicorn marine_forecast.api.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --no-access-log
```

모델이 없으면 `scripts/export_model.py`로 먼저 생성한다.
관측이 오래됐으면 별도 수집기를 실행해야 한다. 이번 프론트 구현에서 API 키를 읽거나
최신 관측을 자동 수집하지 않았다. QC 실험 옵션이 false이면 차단 사유를 정상 표시한다.

두 번째 터미널:

```bash
cd frontend
npm ci
npm run dev
```

브라우저: `http://127.0.0.1:3000`.
API origin의 기본값은 `http://127.0.0.1:8000`이다.
필요하면 `frontend/.env.local`에 다음 서버 설정을 직접 작성하고 Next.js를 재시작한다.

```dotenv
MARINE_API_BASE_URL=http://127.0.0.1:8000
```

프로덕션 빌드 검증:

```bash
npm run typecheck
npm test
npm run build
npm run start
```

`typecheck`는 `next typegen` 후 `tsc --noEmit`을 실행한다. 이는 새 checkout에서
Next가 생성하는 route 타입이 아직 없는 경우에도 타입 검사를 재현하기 위해서다.
별도 Next 서버를 켠 상태에서 빌드 결과를 덮어쓰지 말고, 기존 서버를 중지한 후 빌드한다.
종료는 각 터미널에서 Ctrl+C다.

Next 서버와 API가 다른 머신·컨테이너라면 127.0.0.1이 같은 서비스를 가리키지 않는다.
API origin을 해당 네트워크의 실제 주소로 지정해야 하며, 외부 배포는 다음 단계 범위다.

## 7. 검증 기록

### 프론트 자동 검사

- `npm run typecheck`: 통과.
- `npm test`: 15개 수집·통과, 실패 0개, 제외 0개.
- `npm run build`: Next.js 16.4.3 프로덕션 빌드 통과.
- 테스트는 합성 API 응답과 대체 fetch를 사용한다. 실제 기상청 API를 호출하지 않는다.
- 관측/예측 시간·단위·QC 문구·등급, 정책 차단 시 값 숨김,
  결측과 지연, 부분 HTTP 실패, timeout, 응답 계약과 비밀값 비노출을 검사한다.
- React 정적 렌더 검사는 브라우저 클릭 테스트와 별개다.

### 실제 로컬 연결

- FastAPI 8100 → Next.js 3100으로 별도 임시 프로세스를 실행했다.
- 실제 저장 DB와 모델을 사용했으며 새 기상청 API 호출·재학습은 하지 않았다.
- API는 오래된 실제 관측에 `stale`, 예측에는 `unavailable/stale_observation`을 반환했다.
- Next.js HTTP 200과 한국어 페이지 렌더를 확인했다.
- 데스크톱 브라우저에서 관측값·지연 안내·예측 숫자 `—`·등급 없음·시각을 확인했다.
- 수동 새로고침 버튼을 클릭했고 브라우저 페이지가 유지됨을 확인했다.
- 모바일용 CSS는 구현했지만 브라우저 도구의 viewport 변경이 실제 화면에 적용되지 않아
  정확한 모바일 화면·터치 검증은 완료로 기록하지 않는다.
- 스크린샷은 Git에서 제외된 `data/runtime/frontend-preview.jpg`에 저장했다.

### 기존 Python 회귀 결과와 분리

관련 API·분류 검사:

```bash
uv --cache-dir /tmp/marine-forecast-uv-cache run --locked --no-sync pytest \
  tests/test_reference_levels.py tests/test_api.py tests/test_api_settings.py -q
```

78개 수집·통과, 실패 0개, 외부 API 사용 없음.

전체 회귀는 761개 수집 중 751개 통과, 실제 API 전용 3개 제외,
`tests/test_baseline.py`의 setup 오류 7개였다. 이 파일은 프론트 구현 시작 전에
사용자가 변경한 파일이며, API 테스트가 baseline 파일에도 추가돼 있지만
그 파일에는 `resources` fixture가 없다. 정상 assertion 실패가 아니라 fixture
준비 단계 오류다. 동일 API 테스트가 있는 `tests/test_api.py`는 통과했다.
사용자 변경을 임의로 삭제·이동하지 않고 보존했다. 따라서 전체 Python 회귀가
모두 통과했다고 보고하지 않는다.

## 8. 완료 상태와 다음 단계

- [x] Next.js 단일 페이지·서버 API 조회
- [x] 관측·예측·모의 구간·시각·QC 안내
- [x] 결측·지연·정책 차단·부분 실패·timeout 표시
- [x] 수동 새로고침·로딩·오류 경계
- [x] 타입 검사·프론트 테스트·프로덕션 빌드
- [x] 저장된 실제 관측으로 로컬 API 연결·데스크톱 확인
- [ ] 모바일 브라우저 수동 검증
- [ ] 기존 baseline 테스트 fixture 오류 정리
- [ ] 최신 API 수집을 포함한 전체 운영 흐름·외부 배포
- [ ] 과거 관측 API와 시계열 그래프

이 구현은 사이트 호스팅·인터넷 공개를 하지 않는다. 인증·TLS·속도 제한·배포 네트워크는
별도로 설계해야 한다. 프론트가 생겼다고 실험 모델의 QC·예측 신뢰도가 달라지지 않는다.
