# Phase H: FastAPI로 관측·예측 조회 연결

작성일: 2026-10-24.

후속 변경: 인증된 수동 수집 POST가 추가됐다. 아래 조회 중심의 최초 설계와 구분하며,
현재 실행 설정과 쓰기 요청의 보호 장치는 [수동 관측 수집 안내](manual_collection.md)를 따른다.

## 1. 목표와 전체 흐름

현재 목표는 웹 화면 개발이 아니라 HTTP 요청으로 기존 실험 결과를 조회하는 것이다.

```text
Phase E: 기상청 → 원문 파서 → SQLite 수집
→ Phase F: 정규화·시간·결측 검사
→ Phase G: 학습 모델 저장·로딩·추론
→ Phase H: HTTP 요청·응답 계약과 오류 처리 ← 이번 구현
→ Phase I: 화면·배포·시연
```

HTTP 요청 안에서는 외부 기상청 API를 호출하지 않고, DB를 초기화하거나
자료를 저장하지 않으며, 모델을 재학습하지 않는다. 수집기는 별도 프로세스다.
관측 DB에 저장된 최신 수신 버전을 읽으므로 과거 수신 이력 전체를 재현하지는 않는다.

실험 예측과 운영 안전 판정을 구분한다. QC는 미확인이고
`operational_use_allowed=false`를 유지한다. 모의 참고 등급은 추가하지 않았다.

## 2. 파일과 관심사 분리

| 파일 | 단일 책임 | 기존 파일에 넣지 않은 이유 |
|---|---|---|
| `api/settings.py` | 서버 환경변수·경로·정책 검증 | 수집 CLI와 HTTP 서버의 설정·권한이 다름 |
| `api/schemas.py` | 요청 결과·오류의 자료형과 HTTP 계약 | pandas·모델 로직에 HTTP 표현을 섞지 않음 |
| `api/service.py` | 최신 관측 조회와 기존 추론 호출 | 경로·응답 직렬화 없이 서비스 흐름을 테스트 가능 |
| `api/app.py` | 앱 팩토리·lifespan·라우팅·예외 처리 | HTTP 책임을 학습·수집 코드에서 분리 |
| `api/__init__.py` | API 패키지 표시 | Python 패키지 경계를 명시 |
| `tests/test_api_settings.py` | 설정·응답 수치의 단위 테스트 | 파일·네트워크 없이 빠른 검증 |
| `tests/test_api.py` | 실제 앱·임시 DB·저장 모델 통합 테스트 | HTTP 직렬화와 컴포넌트 연결까지 확인 |

수정한 기존 파일:

- `pyproject.toml`: 런타임에 FastAPI·Pydantic·Uvicorn, 개발 의존성에 httpx2 추가.
- `uv.lock`: 해결된 버전을 고정. 기존 ML 라이브러리 버전은 유지.
- `.env.example`: API 환경변수 예시 추가. 실제 `.env`나 인증키는 수정하지 않음.
- `README.md`: 이 문서 링크 추가.

이전 기압 대조 변경은 보존했다. 모델·데이터·수집기·Phase F/G 함수는
이번 HTTP 연결을 위해 수정하지 않았다. 서버는 기존 `predict_latest()`를 호출한다.

## 3. H1: 요청·응답·오류 계약

### 제공 경로

| 메서드·경로 | 의미 |
|---|---|
| `GET /health/live` | HTTP 프로세스가 응답 가능한지 확인 |
| `GET /health/ready` | 모델이 로딩됐고 관측 저장소를 읽을 수 있는지 확인 |
| `GET /v1/stations/22101/observations/latest` | 최근 24시간 내 최신 관측 |
| `GET /v1/stations/22101/predictions/latest` | 최신 정시 기준 6시간 뒤 실험 예측 |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | 기계가 읽는 OpenAPI 계약 |

초기 지원 관측소는 22101 한 곳이다. 숫자로 된 다른 지점은 404,
숫자가 아닌 지점 인자는 422다. 요청 본문·쿼리 옵션은 받지 않는다.
쿼리로 `allow_unverified_source=true`를 보내도 422이며 정책은 바뀌지 않는다.
조회 경로에 POST 등의 쓰기 메서드를 사용하면 405다.

### 주요 문법

- `BaseModel`: 응답의 구조를 Pydantic으로 검사하고 JSON으로 변환한다.
- `ConfigDict(extra="forbid")`: 계약에 없는 필드가 섞이면 조용히 숨기지 않고 오류로 처리한다.
- `allow_inf_nan=False`: JSON 수치에 NaN·무한대를 허용하지 않는다.
- `Field(ge=0)`, `Field(gt=0)`: 파고·풍속은 0 이상, 기압은 0 초과인지 검사한다.
- `float | None`: 실제 수치 또는 결측을 표현하며 JSON에서는 `null`이다.
- `AwareDatetime`: 시간대 없는 날짜를 응답 계약에서 거부한다.
- `Literal[False]`: 이 버전은 운항 안전 판단에 사용할 수 없다는 계약을 고정한다.
- `Field(default_factory=list)`: 응답 인스턴스마다 새 리스트를 만들어 공유를 피한다.
- `Annotated[실험성공 | 예측불가, Field(discriminator="status")]`:
  `status` 값으로 응답 형태를 선택한다. 예측 불가 응답에는 예측 수치 필드가 없다.

HTTP path는 FastAPI의 `Path(pattern=...)`로 식별자 형식을 검사한다.
현재는 읽기 경로와 서버 고정 설정만 필요하므로 별도 POST 요청 모델은 만들지 않았다.

### HTTP 성공과 예측 성공은 다르다

| HTTP | body 상태 | 의미 |
|---|---|---|
| 200 | 관측 `available` | 수치 3개가 있고 최신성 조건 만족. QC 정상이라는 뜻 아님 |
| 200 | 관측 `incomplete` | 최신 관측은 있지만 필요한 수치가 결측 |
| 200 | 관측 `stale` | 오래된 값. 관측값은 보여주되 지연을 표시 |
| 200 | 관측 `unavailable` | 최근 24시간 관측 없음 |
| 200 | 예측 `experimental` | 실험 예측 실행 성공 |
| 200 | 예측 `unavailable` | 정책 차단·결측·지연 등 명시적인 예측 불가 |
| 404 | `error` | 미지원 관측소·경로 |
| 405 | `error` | 지원하지 않는 HTTP 메서드 |
| 422 | `error` | 잘못된 path·지원하지 않는 쿼리 |
| 503 | `error` | DB 접근 불가·손상, 모델 미로딩, 추론 입력 계약 오류 |
| 500 | `error` | 처리하지 못한 내부 오류 |

예측을 못 한다는 사실도 정상적으로 조회할 수 있으므로 사업 로직상 예측 불가는
200과 `status/reason`으로 표현한다. 인프라 장애인 503과 혼동하지 않는다.

공통 오류 예시:

```json
{
  "status": "error",
  "error": {
    "code": "model_unavailable",
    "message": "A trusted compatible model is not loaded.",
    "details": []
  }
}
```

입력 검증 오류의 details에는 위치와 오류 종류만 넣는다.
입력값·요청 본문·DB 절대경로·인증키·원래 예외 메시지는 오류 응답에 넣지 않는다.
내부 오류 로그도 예외 클래스 이름만 남긴다. 프록시나 Uvicorn 접근 로그는 별도이므로
비밀값을 URL로 전송하지 말고 아래 실행 예시처럼 `--no-access-log`를 사용할 수 있다.

## 4. H2: 최신 관측 조회

```text
station_id 검증
→ 서버 현재 UTC 시각 확정
→ now-24h 이상 now 이하 SQLite 조회
→ 마지막 관측 한 건 정규화
→ 결측·경과시간 검사
→ Pydantic 응답 검사
→ HTTP JSON
```

입력은 관측소와 서버가 주입하는 시계다. 반환값은 `ObservationResponse`다.
정상 흐름은 `ForecastService.latest_observation()`이 저장소의 정렬된 결과에서
마지막 행을 선택하고 `normalize_live_observations([최신행])`을 호출하는 것이다.

관측 조회는 30분 자료도 보여준다. 예를 들어:

```text
09:00 파고 0.5m
09:30 파고 0.7m
09:40 조회
→ 최신 관측: 09:30, 0.7m, is_hourly=false, 경과 10분
```

보고서 `rows=1`은 최근 관측 전체 수가 아니라 이번 응답에서 정규화한 한 행의 수다.
이 보고서와 기압 대조 범위도 그 한 행을 기준으로 한다.

결측 예시:

```json
{
  "status": "incomplete",
  "reason": "missing_observation_values",
  "missing_fields": ["wind_speed_mps"],
  "observation": {
    "wind_speed_mps": null
  }
}
```

위 JSON은 핵심 필드 발췌이며 전체 응답은 OpenAPI와 실행 결과를 따른다.
`pd.NA`는 Python `None`으로 바꾼 뒤 JSON `null`로 직렬화한다. 0으로 바꾸지 않는다.

오래됨과 결측이 동시에 있으면 `status=stale`, `is_stale=true`에 더해
`missing_fields`와 null을 모두 유지한다. 한 사유 때문에 다른 정보를 버리지 않는다.
24시간보다 오래된 자료는 이 조회 범위 밖이므로 최근 관측 없음으로 반환한다.

## 5. H3: 기존 추론 함수 재사용

```text
station_id 검증
→ 시작 시 로딩한 모델 확인
→ predict_latest(store, bundle, now, 서버 정책)
→ ExperimentalPrediction 또는 UnavailablePrediction 검증
→ HTTP JSON
```

서비스는 기존 특성·모델 로직을 복사하지 않는다.
`predict_latest()`가 기존과 동일하게 정확한 1·3·6시간 전 관측을 연결한다.
따라서 HTTP 계층을 추가했다고 예측 수학·학습 구간·최종 테스트 점수가 바뀌지 않는다.

앞의 09:40 조회 예에서 관측 API는 09:30을 보여주지만,
예측 API는 최신 정시 09:00을 기준으로 15:00을 예측한다.
이는 실행 시각부터 정확히 6시간 뒤의 예측도, 향후 6시간의 최대 파고도 아니다.

성공 응답 발췌:

```json
{
  "status": "experimental",
  "reason": null,
  "station_id": "22101",
  "horizon_hours": 6,
  "observed_at_kst": "2026-10-24T05:00:00+09:00",
  "target_at_kst": "2026-10-24T11:00:00+09:00",
  "predicted_significant_wave_height_m": 0.18384281104828545,
  "quality_status": "unverified",
  "operational_use_allowed": false
}
```

응답에는 입력 특성·원본 QC 문자열·기압 대조 근거·정규화 사유도 보존한다.
OpenAPI의 성공/실패 분기 덕분에 프론트는 `status`를 확인한 다음 숫자에 접근할 수 있다.

## 6. H4: 서버 설정·모델 생명주기·상태 점검

### 서버 설정

| 환경변수 | 기본값 | 의미 |
|---|---|---|
| `MARINE_API_DB_PATH` | 프로젝트 `data/runtime/buoy_observations.sqlite3` | 수집 DB |
| `MARINE_API_MODEL_PATH` | 프로젝트 `artifacts/rf_22101_v1` | 로컬 모델 디렉터리 |
| `MARINE_API_TRUST_LOCAL_MODEL` | false | 본인이 만든 pickle을 신뢰해 로딩할지 |
| `MARINE_API_ALLOW_UNVERIFIED_SOURCE` | false | 미확인 QC 등을 수용하는 실험 경로 허용 |
| `MARINE_API_MAX_AGE_MINUTES` | 90 | 관측 지연 허용치, 0 초과 180 이하 |

bool은 true/false 또는 1/0만 받는다. `yes`, 오타, 빈 값은 시작 시 설정 오류다.
환경변수는 앱 생성 시 읽고 요청에서는 바꿀 수 없다. 변경 후 서버를 재시작한다.
API는 `.env`를 자동으로 읽지 않으며 기상청 인증키도 필요하지 않다.
소스 체크아웃에서는 프로젝트 루트를 기본 경로 기준으로 쓰고, wheel로 설치한
환경에서는 서버 시작 작업 디렉터리를 기준으로 한다. 배포 시에는 두 경로를
환경변수의 절대경로로 명시해 작업 디렉터리 의존성을 없애는 것이 좋다.

`@dataclass(frozen=True)`는 설정 속성 재할당을 막는다.
`__post_init__()`에서 검증하고 `object.__setattr__()`로 경로를 절대경로로 정규화한다.
이는 초기화 중 정규화만을 위한 것이며 요청 중 설정 변경을 허용하는 설계가 아니다.
`@classmethod from_env()`의 `cls`는 인스턴스가 아니라 클래스이며,
환경변수를 읽어 검증된 `ApiSettings` 객체를 반환한다.

### 앱 팩토리와 lifespan

`create_app()`은 앱을 만드는 함수다. import만으로 모델을 로딩하거나 수집하지 않는다.
Uvicorn은 `--factory`로 함수를 호출해 앱을 생성한다.

```text
서버 시작
→ 설정의 trust_local_model 확인
→ 신뢰 설정 시 모델 1회 로딩
→ ForecastService를 app.state에 보관
→ yield: HTTP 요청 처리 기간
→ finally: 서비스·모델 참조 정리
```

`@asynccontextmanager`는 yield 전후를 시작·종료 처리로 나눈다.
여기서 yield는 일반 함수의 최종 return과 달리 실행을 일시 중단한다.
종료 시 finally 구문으로 돌아와 정리한다.

`Depends(service)`는 현재 앱의 서비스를 경로 함수에 주입한다.
모델은 worker 프로세스마다 한 번만 읽는다. 요청에서는 `predict()`만 호출한다.
모델 파일을 교체해도 자동 재로딩하지 않으므로 서버 재시작이 필요하다.

모델이 없거나 신뢰하지 않거나 손상됐어도 관측 조회와 liveness는 유지한다.
이때 prediction과 readiness는 503이다. 로딩 실패 후 자동 재시도하지 않는다.
pickle은 코드 실행 위험이 있으므로 본인이 생성한 모델만 명시적으로 신뢰해야 한다.

SQLite·pandas·예측은 동기 작업이라 경로 함수는 일반 `def`로 작성했다.
FastAPI가 동기 경로를 스레드풀에서 처리하므로 이를 `async def` 내부에서 직접
장시간 실행해 이벤트 루프를 막는 구조를 피했다. 대규모 추론 처리량은 별도 검증 대상이다.

### liveness와 readiness의 한계

- `/health/live`: 웹 프로세스의 응답 가능 여부.
- `/health/ready`: 모델 로딩 및 저장소 조회 가능 여부.
- DB가 초기화돼 있으나 비어 있거나 자료가 오래돼도 ready일 수 있다.
- ready는 예측 가능·관측 최신성·QC 정상·수집기 생존을 보장하지 않는다.
- 수집기 상태 점검은 기존 수집기 운영 정책을 따른다.
- DB는 조회할 때 연결해 닫는다. 요청에서 테이블 초기화나 UPSERT를 하지 않는다.
- `Cache-Control: no-store`로 오래된 응답 재사용을 억제한다.

## 7. 실행 명령과 예상 결과

프로젝트 루트에서 잠금 파일에 맞게 설치한다.

```bash
uv sync --locked
```

기본 앱을 실행하면 관측 조회는 가능하지만 모델을 신뢰하지 않았으므로
예측 조회는 503이다. 모델 산출물이 없다면 Phase G 안내대로 먼저 생성한다.

직접 만든 모델을 신뢰하고 QC 차단 응답부터 확인:

```bash
export MARINE_API_TRUST_LOCAL_MODEL=true
export MARINE_API_ALLOW_UNVERIFIED_SOURCE=false
uv run --locked uvicorn marine_forecast.api.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --no-access-log
```

모델·DB가 준비됐다면 예측 조회는 HTTP 200이며
`status=unavailable`, `reason=source_policy_unverified`다.
모델이 로딩되지 않으면 정책 차단 이전에 503을 반환한다.

명시적인 학습·시연용 실험 예측을 켜려면 서버를 중지하고 다시 실행한다.

```bash
export MARINE_API_TRUST_LOCAL_MODEL=true
export MARINE_API_ALLOW_UNVERIFIED_SOURCE=true
uv run --locked uvicorn marine_forecast.api.app:create_app \
  --factory --host 127.0.0.1 --port 8000 --no-access-log
```

다른 터미널에서:

```bash
curl -sS http://127.0.0.1:8000/health/live
curl -sS http://127.0.0.1:8000/health/ready
curl -sS http://127.0.0.1:8000/v1/stations/22101/observations/latest
curl -sS http://127.0.0.1:8000/v1/stations/22101/predictions/latest
```

브라우저에서는 `http://127.0.0.1:8000/docs`를 연다.
서버 종료는 Ctrl+C다. 수집기는 별도 터미널에서 기존 명령으로 실행해야 한다.
기본 API는 호스트 DB를 사용하며 Docker named volume DB를 자동 연결하지 않는다.

보안 인증·속도 제한·TLS·CORS 설정을 추가하지 않았으므로 이 상태로 공개 인터넷에
노출하지 않는다. 기본 예시는 loopback에만 바인딩한다. 외부 배포는 Phase I에서 다룬다.

## 8. H5: 테스트 구성과 검증 기록

### 단위 테스트

`tests/test_api_settings.py`: 외부 시스템 없이 환경변수·bool 오타·최신성 설정,
Pydantic 응답의 비유한 값·음수 거부를 확인한다.
`tests/test_api.py`의 서비스 직접 호출 검사도 시계·지점 조건을 확인한다.

### 로컬 통합 테스트

`tests/test_api.py`: 실제 FastAPI/ASGI 앱·임시 SQLite·합성 원문·소형 RF 산출물을 사용한다.
`with TestClient(app)`는 실제 TCP 포트를 열지 않지만 lifespan과 HTTP 라우팅,
검증·직렬화·예외 처리를 실행한다. 단순 route mock만으로 성공시킨 테스트가 아니다.

검증 항목:

- 최신 30분 관측과 정시 예측의 차이, UTC/KST 시각·6시간 목표.
- null 보존, 결측·지연 동시 표시, 미래 관측 제외.
- QC 기본 차단, 쿼리를 통한 실험 모드 변경 거부.
- 빈 DB·미생성 DB·손상 DB, 모델 미신뢰·로딩 실패·실제 손상.
- 모델 worker당 한 번 로딩, 종료 후 참조 정리.
- HTTP 오류 형식·민감한 원문 비노출·OpenAPI 계약.
- API 조회가 DB를 변경하지 않으며 수집 인증키 없이 작동함.

`monkeypatch`는 해당 테스트 동안 로더나 서비스 메서드를 교체한다.
예를 들어 모델 로딩 호출 횟수를 세거나 민감한 문자열이 든 예외를 발생시켜
공개 응답에 노출되지 않는지 검사한다. 테스트 종료 후 자동 복원된다.

의존성 설치 직후 Starlette가 기존 httpx의 폐기 예정 경고를 냈다.
설치된 Starlette의 TestClient 구현·패키지 명세를 확인해 권장 httpx2로 개발
의존성을 변경했고 경고 필터로 숨기지 않았다. 정확한 버전은 `uv.lock`에 고정한다.

```bash
uv run --locked pytest tests/test_api.py tests/test_api_settings.py -q
uv run --locked pytest -q
```

2026-10-24 최종 검증 기록:

| 실행 | 수집 | 통과 | 실패 | 건너뜀 | 외부 API |
|---|---:|---:|---:|---:|---|
| API 전용 2개 파일 | 49 | 49 | 0 | 0 | 사용 안 함 |
| 전체 회귀 | 725 | 722 | 0 | 3 | 사용 안 함 |

실제 실행은 위 명령에 `--cache-dir /tmp/marine-forecast-uv-cache`와
`--no-sync`를 추가한 잠금 환경에서 수행했다. 기본 제외 3개는 기존 실제 API
전용 테스트다. 이번에는 외부 API 전용 테스트를 다시 실행하지 않았다.
httpx2 전환 이후 테스트 경고는 없었다.

### 실제 TCP 스모크 검증

2026-10-24에 임시 loopback 포트에서 실제 Uvicorn 프로세스를 실행했다.
HTTP는 Python 표준 `urllib.request.urlopen()`으로 요청했다.

- `/health/live`: 200, `ok`.
- `/health/ready`: 200, `ready`.
- 최신 관측: 200, `available`.
- 최신 예측: 200, `experimental`, 약 0.1838428110m.
- 예측 기준 2026-10-24 05:00 KST, 목표 11:00 KST.
- `/openapi.json`: 200.
- 기존 호스트 DB에 저장된 실제 관측과 기존 모델 산출물을 사용했다.
- 외부 기상청 API를 새로 호출하지 않았고 모델을 재학습하지 않았다.
- 검증 후 해당 임시 서버 프로세스를 종료해 상주 서버를 남기지 않았다.

이는 HTTP 연결 성공 기록이며 예측 대상시각 실제값과의 정확도 평가가 아니다.
관측이 오래되면 같은 명령도 이후에는 정상적으로 stale/unavailable을 반환할 수 있다.

## 9. 완료 판정과 남은 범위

- [x] H1 응답·오류 계약 및 서버 설정
- [x] H2 최신 관측 조회
- [x] H3 기존 추론 재사용
- [x] H4 결측·지연·실험 상태와 인프라 오류 구분
- [x] H5 로컬 자동 테스트·실제 TCP 연결 검증
- [ ] 원격 서버·Docker API 배포
- [ ] 공개 서비스 인증·속도 제한·TLS·부하 검증
- [ ] 화면·그래프·모의 참고 등급
- [ ] AQC/MQC 공식 코드 해석과 운영 예측 정책 확정

공식 설명 참고:
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[오류 처리](https://fastapi.tiangolo.com/tutorial/handling-errors/),
[테스트](https://fastapi.tiangolo.com/tutorial/testing/).
