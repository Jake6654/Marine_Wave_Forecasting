# 새 관측 수집 버튼과 FastAPI 연결

작성일: 2026-10-24. 로컬 실행용이며 외부 배포는 수행하지 않았다.

## 1. 무엇이 달라졌나

기존 **새로고침**은 SQLite에 이미 저장된 자료를 다시 조회한다.
새 **새 관측 수집** 버튼은 기상청에 이미 생성된 덕적도 최근 12시간 자료를
가져와 같은 SQLite에 저장한 뒤 관측·예측 화면을 갱신한다.
부이에 새로운 측정을 지시하는 기능은 아니다. 따라서 성공해도 관측시각이 같을 수 있다.

```text
브라우저: 새 관측 수집 클릭
→ Next.js POST /api/observations/collect: 동일 출처 확인
→ FastAPI POST /v1/stations/22101/observations/collect: 서버 간 토큰 확인
→ 잠금 획득·30초 재호출 제한 확인
→ 기상청 최근 12시간 조회 → 기존 파서 → SQLite UPSERT
→ 수신 건수·최신 관측시각 반환
→ router.refresh() → 기존 관측·예측 GET 다시 실행
```

입력은 버튼 클릭뿐이다. 브라우저는 API 키, 토큰, DB 경로, 임의의 관측소나 URL을
선택하거나 보내지 않는다. 지원 관측소는 22101 하나다.
출력은 수집 상태·건수·시작/완료 UTC 시각·최신 관측 KST 시각이다.
기존 모델을 다시 학습하지 않으며, 저장 후 조회 과정에서 기존 추론 함수를 재사용한다.

## 2. 파일별 책임

| 파일 | 역할 |
| --- | --- |
| `src/marine_forecast/data/buoy_collection.py` | API 조회·저장·재조회 확인의 공통 수집 함수 |
| `scripts/collect_buoy.py` | 기존 CLI 입출력 유지, 공통 수집 함수 호출 |
| `src/marine_forecast/api/collection.py` | 인증·잠금·재호출 제한·안전한 오류 변환 |
| `src/marine_forecast/api/settings.py` | 서버 토큰 설정 및 검증 |
| `src/marine_forecast/api/schemas.py` | 수집 응답 계약 |
| `src/marine_forecast/api/app.py` | POST 라우트와 수집 서비스 생명주기 연결 |
| `frontend/app/api/observations/collect/route.ts` | 서버 전용 환경 변수를 읽는 Next.js 라우트 |
| `frontend/lib/manual-collection.ts` | 출처 확인·서버 간 POST·응답 검증·오류 정제 |
| `frontend/lib/collection-contract.ts` | 브라우저 요청·Zod 응답 계약·한국어 오류 안내 |
| `frontend/components/collect-button.tsx` | 중복 클릭 방지·진행 상태·결과 안내·화면 갱신 |

수집 로직을 API 파일에 복사하면 CLI와 웹의 조회 범위·저장 동작이 달라질 수 있다.
따라서 패키지에 공통 함수를 추출하고 CLI는 얇은 래퍼로 유지했다.
HTTP 정책은 데이터 수집 함수에 넣지 않고 API 계층에서 담당한다.

## 3. 반드시 필요한 설정

실제 `.env`, `frontend/.env.local`은 이번 작업에서 수정하지 않았다.
실행 중이던 사용자 8000·3000 포트 서버도 종료하거나 재시작하지 않았다.
새 라우트와 환경 변수를 적용하려면 설정 후 두 서버를 재시작한다.

### 3-1. 서버 간 토큰 생성

```bash
openssl rand -hex 32
```

출력된 임의 문자열을 **동일하게** 두 파일에 적는다. 예시 문구 자체를 토큰으로 쓰지 않는다.
32자 이상 ASCII 출력 가능 문자여야 하며 임의 생성값을 권장한다.

프로젝트 루트 `.env`:

```dotenv
KMA_API_KEY=기존_기상청_인증키
MARINE_COLLECTION_TOKEN=위에서_생성한_토큰
```

`frontend/.env.local`:

```dotenv
MARINE_API_BASE_URL=http://127.0.0.1:8000
MARINE_COLLECTION_TOKEN=동일한_토큰
```

기상청 인증키는 Python 서버에만 필요하다. 프론트엔드 환경 파일이나
`NEXT_PUBLIC_*`, URL, 브라우저 요청에 넣지 않는다. 토큰도 `NEXT_PUBLIC_*`로 만들지 않는다.
두 실제 환경 파일은 Git에서 제외한다. 쉘에 기존 토큰을 export했다면
Next.js 환경 파일보다 우선할 수 있으므로 동일한 값인지 확인한다.

### 3-2. FastAPI 실행

프로젝트 루트에서, 본인이 관리하는 `.env`만 로드한다.

```bash
set -a
source .env
set +a
export MARINE_API_TRUST_LOCAL_MODEL=true
export MARINE_API_ALLOW_UNVERIFIED_SOURCE=true
uv run --locked uvicorn marine_forecast.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

모델 신뢰 설정은 직접 만든 로컬 artifact에만 사용한다.
미확인 자료 허용은 **실험용 예측**을 켜는 기존 명시적 선택이며 공식 품질 검증을 의미하지 않는다.
이 설정을 끄면 수집은 가능하지만 기존 정책에 따라 예측이 차단될 수 있다.

### 3-3. Next.js 실행

별도 터미널에서:

```bash
cd frontend
npm run dev
```

`http://127.0.0.1:3000`에서 **새 관측 수집**을 클릭한다.
토큰이 없으면 기능은 기본적으로 꺼져 있고 안내 오류가 표시된다.

## 4. 정상 흐름과 실패 흐름

버튼은 요청 중 비활성화된다. `useRef`로 React 재렌더링 전의 빠른 연속 클릭도 막는다.
성공하면 건수와 최신 시각을 알리고 `router.refresh()`로 서버 컴포넌트를 다시 조회한다.
화면 진입이나 일반 새로고침만으로 수집을 자동 실행하지 않는다.

| 상태 | 의미 | 조치 |
| --- | --- | --- |
| 403 | 토큰 불일치 또는 잘못된 출처 | 두 서버 설정·접속 주소 확인 |
| 409 | 다른 수집 작업이 잠금 보유 | 실행 중인 수집기 확인 |
| 429 | 이전 시도 후 30초 미경과 | 잠시 기다린 뒤 수동 재시도 |
| 502 | 기상청 실패·서버 연결 또는 타임아웃 | 연결 상태 확인, 자동 재시도 없음 |
| 503 | 토큰/인증키 미설정 또는 저장소/상태 오류 | 서버 설정과 저장 경로 확인 |

Python은 프로세스 내부 `threading.Lock`과 프로세스 간 파일 잠금을 함께 사용한다.
잠금은 DB 경로의 확장자를 `.collector.lock`으로 바꾼 파일이다.
기존 **반복 수집기**는 실행 중 내내 같은 잠금을 유지하므로, 함께 켜져 있으면
수동 요청은 409가 된다. 이때는 일반 새로고침으로 수집 결과를 조회한다.
모든 독립 CLI 실행이 자동으로 이 잠금을 사용한다는 의미는 아니다.

30초 제한은 `.manual-collection.json`에 마지막 시도 시각을 원자적으로 저장한다.
여러 API 인스턴스·재시작에도 공유하며, 외부 조회가 실패한 시도도 포함한다.
손상된 상태 파일은 임의로 무시하지 않고 오류로 보고한다.
`finally`와 컨텍스트 매니저가 성공·실패 모두에서 잠금을 해제한다.

Next.js는 최대 60초, 브라우저는 65초를 기다린다.
연결이 끊겨도 이미 시작한 Python 수집이 계속될 수 있어 자동 재시도하지 않는다.
정확히 한 번 실행을 보장하는 분산 작업 큐를 구현한 것은 아니다.
조회 실패로 기존 DB 관측을 삭제하지 않는다.

## 5. 보안과 데이터 해석 범위

- FastAPI 토큰은 서버 간 공유 비밀이며 설정 객체 repr·응답에 노출하지 않는다.
- 응답은 허용된 필드만 전달하고 원문 예외·키·DB 경로를 브라우저에 반사하지 않는다.
- Next.js는 Origin과 Host, 전용 요청 헤더를 확인하며 localhost/127.0.0.1/::1만 허용한다.
- **공개 배포용 사용자 인증은 아니다.** 로컬 사이트 접근자는 버튼을 사용할 수 있다.
  외부 배포에는 별도 로그인·권한·HTTPS·사용자별 호출 제한 설계가 필요하다.
- 원문 QC 보존, 결측 정책, KST→UTC, 정시 관측만 추론, 정확한 과거 시각 결합,
  90분 최신성 검사와 실험용 표시를 그대로 유지한다.
- 수집 성공이 예측 성공을 보장하지 않는다. 최신성·과거 입력·모델·품질 정책을
  통과하지 못하면 예측 불가 상태를 유지한다.
- 관측 최신 시각과 예측 기준시각은 다를 수 있다. 예측은 기준 관측의 정확히 6시간 뒤다.

## 6. 검증 기록

### 자동 테스트

```bash
uv --cache-dir /tmp/marine-forecast-uv-cache run --locked --no-sync pytest tests/test_manual_collection.py tests/test_collect_buoy.py tests/test_api.py tests/test_api_settings.py -q
```

73개 수집, 73개 통과, 0개 실패. 외부 API는 가짜 클라이언트로 대체하고
실제 임시 SQLite와 FastAPI TestClient 연결을 검증했다.
인증·설정·동시 요청·반복 수집기 잠금·프로세스 간 재호출 제한·실패 후 잠금 해제를 포함한다.

```bash
cd frontend
npm test
npm run typecheck
MARINE_NEXT_DIST_DIR=.next-check npm run build
```

프론트엔드 23개 통과, 0개 실패. 응답 계약, 출처 검사, 토큰 주입,
오류 정제, 타임아웃, 브라우저 요청을 검증했다. 타입 검사와 production 빌드도 통과했다.
`.next-check`는 실행 중인 사용자 개발 서버의 `.next`를 덮어쓰지 않기 위한 별도 빌드 경로다.

전체 Python 회귀 결과는 772개 중 **762 통과, 3 건너뜀, 7 setup 오류**다.
7개는 이번 변경 이전에도 있던 `tests/test_baseline.py`의 `resources` fixture 부재이며,
관련 없는 사용자 테스트를 임의 삭제·수정하지 않았다. 전체 통과 상태는 아니다.

### 실제 외부 시스템 검증

사용자의 기존 8000/3000 서버를 건드리지 않고 임시 8101/3101 서버로 검증했다.
인증키는 Python 프로세스에만, 임시 임의 토큰은 두 서버 메모리에만 전달했다.
브라우저 클릭 → 실제 기상청 조회 → 기존 runtime SQLite 저장 → 관측·예측 표시를 확인했다.

2026-10-24 18:03 KST 확인 결과:

| 항목 | 수집 전 | 수집 후 |
| --- | --- | --- |
| 최신 관측시각 | 2026-10-24 05:00 KST | 2026-10-24 18:00 KST |
| 유의파고 | 0.2m | 0.4m |
| 6시간 뒤 예측 | 오래된 관측으로 차단 | 약 0.386m, 실험 예측 |
| 예측 대상시각 | 과거 기준 | 2026-10-25 00:00 KST |

브라우저 검사에서 Next.js 내부 URL의 localhost와 실제 Host의 127.0.0.1 차이로
정상 클릭이 차단되는 문제를 발견했다. Origin을 브라우저 Host와 비교하도록 고치고
로컬 호스트 제한을 유지했으며 회귀 테스트를 추가했다.
시연 화면은 Git 제외 경로 `data/runtime/manual-collection-preview.jpg`에 저장했다.
모바일 실기기·공개 배포·인증된 다중 사용자 운영·기상청 장애 실환경 재현은 검증하지 않았다.
