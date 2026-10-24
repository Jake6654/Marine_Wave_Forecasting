# 무료 Render 배포: 모델 공급과 시작 시 관측 복구

작성일: 2026-10-24. 실제 Render 배포 전의 로컬 구현·검증 기록이다.

## 1. 목표와 현재 위치

```text
기존: 로컬 모델·SQLite → FastAPI → 로컬/배포 Next.js
→ 이번: Git 배포용 모델 + API 시작 시 비동기 관측 복구·반복 수집
→ 다음: 코드 push → Render 무료 배포 → 모델·관측 검증
→ 최종: Vercel 조회 연결 → 공개 수집 버튼의 인증·출처 정책 별도 구현
```

무료 서버의 임시 파일시스템이 사라져도 Git에 포함된 동일 모델을 다시 받으며,
관측 DB는 기상청 최근 12시간을 다시 수집해 복구한다.
매 시작마다 모델을 재학습하거나 최종 테스트 데이터로 다시 평가하지 않는다.
Render 무료 서비스의 비활성 종료를 막거나 외부 요청으로 계속 깨우는 구현은 하지 않는다.

## 2. 파일과 책임

| 파일 | 책임 |
| --- | --- |
| `deployment/models/rf_22101_v1/model.pkl` | 기존 200-tree RandomForest 복사본 |
| `deployment/models/rf_22101_v1/metadata.json` | 버전·입력 계약·해시·출처 |
| `deployment/README.md` | 모델 배포·신뢰 범위 설명 |
| `src/marine_forecast/api/background_collection.py` | 즉시 1회 및 10분 간격 수집, 실패 격리, 종료 |
| `src/marine_forecast/api/settings.py` | 기본 false인 백그라운드 수집 설정 |
| `src/marine_forecast/api/app.py` | FastAPI lifespan에 작업 시작·종료 연결 |
| `tests/test_background_collection.py` | 수명주기·실패·모델·DB·HTTP 연결 검사 |

기존 `artifacts/` 전체의 Git 제외 규칙은 유지한다. 모델 두 파일만 새 배포 디렉터리에
복사했다. 인증키·토큰·원본 CSV·실시간 DB는 모델 배포 폴더에 넣지 않는다.
약 6MB의 모델은 Git 배포 대상이며 공개 저장소라면 모델도 공개된다는 점에 유의한다.

## 3. 실행 흐름과 입력·출력

```text
uvicorn 앱 팩토리 실행
→ 환경 변수 검증
→ 신뢰 설정이 true인 기존 모델을 한 번 로딩
→ 백그라운드 수집 설정이 true면 스레드 시작
→ HTTP 서버는 수집 완료를 기다리지 않고 시작

수집 스레드:
종료 여부 확인 → 같은 DB의 파일 잠금 획득
→ KMA 최근 12시간 조회 → 기존 응답 파서 → SQLite UPSERT
→ 잠금 해제 → 완료 시점부터 600초 대기 → 반복
```

입력은 `ApiSettings`, 서버의 `KMA_API_KEY`, 공개 기상청 관측이다.
수집 출력은 동일 API가 읽는 SQLite이며, `collect_once()`는 성공 여부 bool을 반환한다.
중복 관측은 기존 키 `(station_id, observed_at)`로 갱신한다.
모델 입력의 정확한 과거 시각 결합·정시 자료 제한·90분 최신성 검사는 바꾸지 않았다.

시작 직후 DB가 아직 없을 때 `/health/live`는 200이어도 `/health/ready`와 관측 조회는
503일 수 있다. 첫 수집이 끝난 뒤 조회를 다시 한다. 모델이 없거나 환경 버전이 다르면
기존 모델 로더가 거부하므로 서버가 켜졌다고 예측 준비가 끝난 것은 아니다.

예를 들어 18:20 KST에 서버가 켜지면 약 06:20~18:20 구간을 요청한다.
기상청 최신 정시 관측이 18:00이고 정확한 과거 입력이 있으면 18:00을 기준으로
다음 날 00:00 한 시점을 예측한다. 현재 시각부터 6시간 뒤가 아니다.

## 4. 실패·동시 실행·종료

- 수집을 켰는데 KMA 키가 없으면 시작 시 명확한 설정 오류로 중단한다.
- 네트워크·응답 파싱·DB 오류는 수집 작업에서 잡고 타입만 경고 로그에 남긴다.
  원문 예외, 키와 요청 URL은 출력하지 않는다.
- 실패해도 HTTP 서버와 기존 관측은 유지하고 600초 후 재시도한다. 즉시 연속 재시도하지 않는다.
- 기존 자료가 오래되면 기존 최신성 검사로 예측이 차단된다. 실패한 자료를 0으로 대체하지 않는다.
- 같은 파일 잠금을 수집 시도 중에만 잡는다. 수동 수집과 겹치면 한쪽은 대기 없이 거부/건너뛴다.
- 별도 `run_buoy_collector.py`는 실행 중 내내 잠금을 보유한다.
  이 배포 방식에서는 별도 반복 수집기를 함께 실행하지 않는다.
- 종료 시 Event를 설정해 10분 대기를 깨우고 작업 스레드를 최대 20초 기다린다.
  비정상적으로 오래 걸리면 경고한다. 강제 종료나 무료 서버 정지 때 작업 완료는 보장하지 않는다.
- 단일 프로세스·인스턴스 전용이다. SQLite와 이 잠금은 여러 서버 사이의 공유 저장소가 아니다.

`threading.Event.wait(600)`은 메인 HTTP 이벤트 루프를 막지 않는다.
`Thread`는 외부 HTTP/SQLite 같은 동기 I/O를 별도 실행 흐름에서 처리한다.
`asyncio.to_thread(collector.stop)`은 종료 대기 역시 HTTP 이벤트 루프 밖에서 처리한다.
이는 무료 서버가 잠든 동안에도 살아 있는 예약 작업이라는 뜻은 아니다.

## 5. 지금 Render 화면에서 설정할 값

기존 Root Directory는 빈 값, Runtime은 Python 3, Instance는 Free를 유지한다.

Build Command:

```bash
uv sync --locked --no-dev
```

Start Command는 기존 안내 그대로다. 별도 shell 시작 스크립트는 필요 없다.

```bash
uv run --locked --no-dev uvicorn marine_forecast.api.app:create_app --factory --host 0.0.0.0 --port $PORT --workers 1
```

Health Check Path:

```text
/health/live
```

환경 변수 최종 목록:

| Key | Value |
| --- | --- |
| `PYTHON_VERSION` | `3.13.5` |
| `UV_VERSION` | `0.10.10` |
| `KMA_API_KEY` | 발급받은 키, 노출됐다면 교체한 키 |
| `MARINE_COLLECTION_TOKEN` | 직접 생성한 비밀 토큰, 노출됐다면 새 값 |
| `MARINE_API_MODEL_PATH` | `deployment/models/rf_22101_v1` |
| `MARINE_API_DB_PATH` | `/tmp/marine-forecast/buoy_observations.sqlite3` |
| `MARINE_API_TRUST_LOCAL_MODEL` | `true` |
| `MARINE_API_ALLOW_UNVERIFIED_SOURCE` | `true` |
| `MARINE_API_MAX_AGE_MINUTES` | `90` |
| `MARINE_API_BACKGROUND_COLLECTION` | `true` |

즉, 앞서 false로 입력한 두 모델/실험 설정을 true로 바꾸고 경로 2개와
백그라운드 수집 설정을 추가한다. true 설정은 본인이 만든 모델과 실험용 예측에만 적용한다.
기본 차단 정책 자체는 변경하지 않았으며 일반 로컬 실행에는 이 기능이 자동으로 켜지지 않는다.
`MARINE_COLLECTION_TOKEN`은 자동 수집에 필요하지 않고 수동 POST 인증용이다.
Vercel 공개 수집 버튼은 아직 로컬 Origin 제한 때문에 차단된다.

## 6. push 후 배포 확인 순서

1. 새 모델 두 파일이 Git 커밋에 포함됐는지 확인한다. `.env`·DB는 제외한다.
2. Render가 선택한 브랜치에 커밋을 push한다. 이번 작업에서는 커밋/push하지 않았다.
3. Render에서 해당 커밋으로 배포한다.
4. `/health/live`가 200인지 확인한다.
5. 첫 수집 완료 후 `/health/ready`가 200인지 확인한다.
6. `/v1/stations/22101/observations/latest`의 실제 시각과 값을 확인한다.
7. `/v1/stations/22101/predictions/latest`의 experimental 또는 unavailable 이유를 확인한다.
8. Vercel Production의 `MARINE_API_BASE_URL`을 Render HTTPS origin으로 지정 후 재배포한다.

환경 버전 불일치는 모델 메타데이터를 조작해 우회하지 말고 Python/의존성 설치부터 확인한다.
원본 모델은 Python 3.13.5, scikit-learn 1.9.1, pandas 3.0.6이며 `uv.lock`을 사용한다.
모델 파일의 SHA256 확인은 손상 검출이지 외부 pickle을 신뢰할 근거는 아니다.

## 7. 검증

```bash
uv run --locked pytest tests/test_background_collection.py tests/test_api.py tests/test_api_settings.py tests/test_manual_collection.py tests/test_collect_buoy.py -q
```

테스트는 외부 API를 가짜 클라이언트로 대체한다. 실제 SQLite, 배포 모델 파일,
모델 로더, 특성 생성, FastAPI TestClient의 예측 응답을 연결한다.
주기 대기는 가짜 Event로 바꿔 실제 10분을 기다리지 않고 간격과 재시도를 검증한다.
실제 스레드 시작·종료와 느린 수집 중 HTTP liveness 제공도 확인한다.

2026-10-24 로컬 검증 결과:

| 구분 | 수집 | 통과 | 실패/오류 | 건너뜀 |
| --- | --- | --- | --- | --- |
| 위 관련 테스트 | 85 | 85 | 0 | 0 |
| 전체 Python 회귀 (`pytest -q`) | 784 | 774 | 7 setup 오류 | 3 |

실제 실행 시 `uv --cache-dir /tmp/marine-forecast-uv-cache run --locked --no-sync`
접두사를 사용해 준비된 로컬 환경에서 실행했다. 관련 테스트는 2.50초,
전체 회귀는 13.10초 걸렸다. 외부 기상청 API나 Render는 호출하지 않았다.
7개는 변경 전부터 있던 `tests/test_baseline.py`의 `resources` fixture 부재다.
이를 이번 기능의 오류와 구분하고, 관련 없는 기존 테스트는 임의 수정하지 않았다.
따라서 전체 회귀 통과 상태는 아니다.

Render Linux 실행, 무료 인스턴스 메모리, 실제 키 승인/응답, 슬립 후 재시작,
Vercel-Render 조회와 공개 버튼 인증은 실제 배포 후 추가 검증 대상이다.

현재 프론트의 5초 조회 제한은 무료 서버의 cold start를 넘길 수 있다.
이 경우 서버가 깨어난 후 새로고침해야 한다. cold start 대기 UI와 제한 시간 보완은
이번 모델/수집 준비 범위에 포함하지 않았다.
