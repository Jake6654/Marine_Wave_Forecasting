# Phase F–G: 실시간 관측을 6시간 뒤 예측에 연결하기

작성일: 2026-10-24. 이 문서는 구현 안내와 학습용 설명이다.
HTTP API·화면·참고 등급·배포는 Phase H 이후 범위이며 이번에 추가하지 않았다.

## 1. 현재 위치와 완료 범위

```text
2025년 관측으로 입력 생성·학습·평가
→ 실제 API 수집·원문 SQLite 저장
→ F: 원문을 수치 관측으로 정규화
→ G: 특성 생성·모델 로딩·최신 시점 예측 ← 현재
→ H: FastAPI
→ I: 화면·배포·시연
```

이번 구현은 **실험용 end-to-end 경로**다. QC와 기압의 의미가 확인된 운영 경로가 아니다.
기본값은 예측을 차단하고, 사용자가 명시적으로 허용한 실험에서만 예측한다.
`experimental`을 `safe`, `QC 정상`, `공식 예보`로 읽으면 안 된다.

## 2. Phase F의 목표·파일·판정 기준

목표는 원문 보관 상태를 훼손하지 않으면서 기존 특성 생성기로 연결 가능한 수치와
확인 상태를 분리하는 것이다. 수집기의 책임은 HTTP·저장이고, 정규화의 책임은
값 해석이다. 이 로직을 수집기에 넣으면 처리 정책 변경에 따라 원문까지 달라질 수 있다.

- `src/marine_forecast/data/live_normalization.py`: 숫자·결측·기본 범위·QC 원문 보존.
- `src/marine_forecast/inference.py`: DB 조회, 정시 선택, 최신성·특성 완전성 검사.
- `tests/test_live_inference.py`: 외부 시스템 없는 변환 테스트와 임시 DB 연결 테스트.

### F1. 출처·처리 정책

2026-10-24 확인 자료:

1. [기상청 API허브 해양관측](https://apihub.kma.go.kr/apiList.do?seqApi=3)
   - 기간 조회 입력은 KST.
   - `WH_SIG`는 유의파고(m), `WS1`은 센서1 풍속(m/s).
   - 공개 표의 `PA` 설명은 해면기압(hPa).
2. [기상청 데이터위키 해양기상부이 활용 예제](https://datawiki.kma.go.kr/doku.php?id=기상관측:해양:해양기상부이)
   - 같은 `kma_buoy2.php` 자료에서 수치가 -50 이하이면 관측 없음·오류로 보고
     NaN으로 바꾸는 예제를 확인했다. QC 필드는 예제의 수치 표에서 제외된다.

두 번째 페이지는 직접 열기가 실패한 경우가 있어 검색에 제공된 공식 페이지의
예제 본문으로 확인했다. 이 예제가 AQC/MQC 위치별 코드 사전까지 제공한 것은 아니다.

프로젝트 정책 버전은 `kma-buoy-experimental-v1`이다.

| 원문 | 정규화 열 | 정책 |
|---|---|---|
| `WH_SIG` | `significant_wave_height_m` | 유의파고, m |
| `WS1` | `wind_speed_mps` | 센서1만 사용; WS2로 자동 대체하지 않음 |
| `PA` | `api_pressure_hpa` | 현지기압이라고 단정하지 않는 임시 명칭 |
| `AQC`, `MQC` | `aqc_raw`, `mqc_raw` | 문자열 그대로 보존; 위치 의미를 추정하지 않음 |

공통 수치 규칙:

- 빈 값 → 결측, 사유 `empty`.
- 숫자가 아닌 문자열 → 결측, 사유 `invalid_number`.
- NaN·무한대 → 결측, 사유 `non_finite`.
- 유한한 수치 ≤ -50 → 결측, 사유 `source_missing_or_error`.
- 파고·풍속 음수, 기압 0 이하 → 결측, 사유 `invalid_physical_range`.
- 파고·풍속의 0은 정상 수치로 보존한다.
- 실패를 조용히 숨기지 않고 관측소·시각·항목·사유를 `issues`에 기록한다.
- 측정값을 임의로 보간하거나 잘라내거나 센서끼리 대체하지 않는다.

기본 모드는 `source_policy_unverified`로 예측을 차단한다.
실험 허용 모드에서만 `api_pressure_hpa`를 기존 모델의 `station_pressure_hpa`로
연결한다. 이는 값의 의미가 같다고 확정한 변환이 아니라 명시적인 실험적 가정이다.
다른 제공 경로의 값 비교가 일부 일치해도 물리적 의미의 동일성을 증명하지는 않는다.

**F1의 외부 확인 과제는 남아 있다.** AQC/MQC 위치·코드 정의, 기압 항목의
의미와 WS1의 학습 원본 대응을 확정한 운영 정책은 아직 만들지 않았다.
문의나 새로운 모델 설계 없이 이 상태를 완료로 바꾸지 않는다.

### F2. 원문 → 정규화 표

`normalize_live_observations(observations)`

- 입력: `list[RawBuoyObservation]`.
- 출력: `(DataFrame, report)` 튜플.
- 원문 객체는 수정하지 않고 새 표를 만든다.
- `station_id`: pandas `string`.
- `observed_at`: UTC 시간대가 있는 날짜형.
- 수치 3열: nullable `Float64`.
- QC 원문: 문자열.

```text
WH_SIG=".5", WS1="-99", WS2="9", PA="1018"
→ 숫자 해석·결측 규칙
→ 유의파고=0.5, 풍속=pd.NA, API 기압=1018.0
→ issues: WS1/source_missing_or_error
```

`WS2=9`로 풍속을 메우지 않는다. `pd.NA`는 ‘알 수 없는 값’이고 0이 아니다.
`astype("Float64")`의 대문자 F는 결측을 지원하는 pandas 수치 자료형을 뜻한다.
이 표에는 예측 정답이 없다. 실시간 추론에서는 미래 정답이 아직 관측되지 않았다.

식별자·시각과 원문이 불일치하거나 중복 키가 있으면 `ValueError`로 중단한다.
같은 프로젝트 내부의 저장소 검증 함수 `_encode_observation()`을 재사용한다.
언더스코어는 내부 구현용이라는 관례이며 별도 공개 API라는 뜻은 아니다.

### F3. DB 조회와 특성 연결

`predict_latest()`가 요청 시각 이전 24시간을 읽는다.
이는 과거 시점의 수신 이력 재현 기능이 아니다. 저장소는 최신 수신 버전만 보관한다.
테스트의 `now` 주입은 시간 조건을 결정적으로 검증하기 위한 장치다.

```text
SQLite 원문 목록
→ 정규화
→ 정확한 정시 관측만 선택
→ 실험 허용 시 기압 열 연결
→ 기존 build_features()
```

`frame["observed_at"].dt.minute.eq(0)`은 각 행의 ‘분이 0인가’를 Boolean Series로 만든다.
`.loc[조건]`으로 정시 행만 선택한다. 08:30을 09:00으로 반올림하거나 평균내지 않는다.

### F4. 정확한 시간·최신성·완전성

예를 들어 다음 관측이 있다고 하자.

```text
03:00 파고=1.1
06:00 파고=0.8
08:00 파고=0.6
08:30 파고=0.7
09:00 파고=0.5, 풍속=2.0, 기압=1018
```

09시 입력은 다음과 같다.

```text
현재 파고 0.5 / 풍속 2.0 / 실험적 기압 입력 1018
1시간 전 0.6 / 3시간 전 0.8 / 6시간 전 1.1
1시간 변화량 0.5 - 0.6 = -0.1
```

08시가 없으면 08:30이나 바로 앞 행을 대신 쓰지 않는다.
기존 `build_features()`가 시각을 이동시킨 표와 정확한 키로 merge하므로
1시간 전 값은 결측으로 남는다. 최신 행에 결측이 있으면 예측하지 않는다.

`latest.isna().any()`는 열별로 결측이 하나라도 있는지 확인한다.
그 Boolean mask로 `latest.columns[...]`를 선택해 누락된 특성 이름을 반환한다.

최신성 기본값은 90분이다. 이는 공식 기준이 아니라 프로젝트의 시연 정책이다.
설정 범위는 0보다 크고 180분 이하이며, 기준 관측이 오래됐으면 `stale_observation`이다.
현재 시각 이후의 관측은 DB 조회 단계에서 제외한다.

### F5. 테스트·완료 판정·제외 범위

수치와 원문 보존, 0·결측·비유한 값, 중복, 센서 대체 금지,
정시 선택, 정확한 lag 누락, 최신 행 불완전, 오래된 자료, 미래 자료 제외를 테스트한다.
합성 원문과 임시 SQLite를 사용하므로 실제 API 없이 재현 가능하다.

변환·연결 구현의 판정 기준은 통과했지만 공식 QC 의미의 검증은 별개로 남는다.
자동 보간·수동 QC 판정·장비별 보정·다른 관측소 일반화는 제외한다.

## 3. Phase G의 목표·파일·판정 기준

목표는 재학습을 매 요청마다 하지 않고 고정된 학습 모델을 저장·로딩해
가장 최근의 유효 정시 관측에서 정확히 6시간 뒤를 예측하는 것이다.

- `model_artifact.py`: 모델 파일·계약·버전·해시 검증.
- `scripts/export_model.py`: 기존 학습 구간으로 모델 학습·저장·재로딩 대조.
- `inference.py`: 입력 한 행 생성, predict 호출, 결과·경고 조립.
- `scripts/predict_latest.py`: DB·모델 경로와 실행 옵션을 받는 CLI.
- `tests/test_model_artifact.py`, `test_export_model.py`: 산출물과 학습 구간 검증.
- `tests/test_live_prediction.py`: 실제 API부터 예측까지의 opt-in 통합 테스트.

### G1. 고정 모델 산출물

```bash
uv run --locked python scripts/export_model.py
```

출력 디렉터리:

```text
artifacts/rf_22101_v1/
  model.pkl
  metadata.json
```

기존 디렉터리가 있으면 덮어쓰지 않는다. 다른 버전을 만들 때는
`--output artifacts/rf_22101_v2`처럼 새 경로를 사용한다.
생성 파일은 Git에서 제외하며 재생성 코드와 설명을 커밋한다.

학습 표본 3,891개만 사용하고 기존 RF 설정을 유지한다.
검증 1,130개로 기존 검증 점수를 재확인하고 저장 전후 예측 배열이 정확히 같은지
검사한다. 최종 테스트를 재평가하거나 학습+검증 결합 재학습을 하지 않는다.

메타데이터에는 입력 순서·6시간 horizon·관측소·라이브러리 버전·모델 설정,
원본/정제 파일 해시·학습 시간 경계·검증 점수를 기록한다.
모델 ID는 모델 바이트의 SHA-256 앞부분으로 만든다.

`pickle.dumps()`는 모델을 바이트로, `pickle.loads()`는 바이트를 객체로 복원한다.
**pickle 로딩은 임의 코드를 실행할 수 있다.** 본인이 만든 신뢰할 수 있는 파일만
로드해야 한다. SHA-256 검사는 손상 탐지이며 출처 인증이나 전자서명이 아니다.
`trusted=True`와 CLI `--trust-local-model`은 이 신뢰 경계를 명시한다.
Python·pandas·scikit-learn 버전이 다르면 로딩을 거부하므로 배포 환경에서 같은
환경으로 산출물을 다시 만들거나 버전을 맞춰야 한다. 파일을 내려받아 자동 신뢰하지 않는다.

`ModelBundle`의 `@dataclass(frozen=True)`는 model과 metadata 속성 재할당을 막는다.
내부 dict와 모델까지 깊게 불변으로 만드는 기능은 아니므로 직접 변조하지 않는다.

### G2–G3. 모델 입력과 예측

```text
최신 정시 관측
→ 시각·결측 검사
→ FEATURE_COLUMNS 순서의 float64 DataFrame 한 행
→ model.predict(inputs)
→ 첫 예측을 Python float로 변환
```

`model.predict()`의 반환값은 NumPy 배열이다. `[0]`으로 한 행의 결과를 꺼낸다.
RF는 여러 트리의 예측을 평균한다. 여기서는 이미 학습한 모델을 읽기만 하며
API 호출 시 `.fit()`으로 다시 학습하지 않는다.
음수·무한대·NaN 결과는 임의로 0으로 자르지 않고 예측 불가로 반환한다.

### G4. 결과 계약과 실패 흐름

실험 성공 결과에는 다음을 포함한다.

- `status="experimental"`, `quality_status="unverified"`.
- `operational_use_allowed=false`와 확인되지 않은 사항의 경고.
- 관측소·모델 ID·처리 정책 버전.
- 생성 시각, 기준 관측시각, 목표 시각(UTC/KST), 관측 경과시간.
- 예측 파고(m), 현재값 유지 기준 모델의 값.
- 실제 입력 특성, 사용 관측들의 AQC/MQC 원문, 정규화 사유 보고서.

예측 불가 사유:

| reason | 의미 |
|---|---|
| `source_policy_unverified` | 실험 가정을 명시적으로 허용하지 않음 |
| `no_recent_observations` | 최근 24시간에 해당 관측소 자료 없음 |
| `no_hourly_observations` | 정시 자료 없음 |
| `stale_observation` | 최신 정시 관측이 허용 나이 초과 |
| `incomplete_latest_features` | 최신 행에 필요한 입력·정확한 과거값이 없음 |
| `invalid_model_prediction` | 모델 결과가 유효한 파고 수치가 아님 |

이 경우 결과에 예측 숫자를 넣지 않는다. 파일 손상·DB 미초기화·잘못된 옵션 등은
CLI의 `status="error"`로 반환한다. 내부 함수를 직접 쓰면 예외를 처리해야 한다.

CLI 종료 코드는 실험 예측 성공 0, 실행 오류 1, 예측 불가 2다.
0도 운영 안전성을 뜻하지 않는다.

### G5. 실행 및 검증 방법

이전에 설정한 인증키를 불러온 뒤 수집한다. 키를 출력하지 않는다.

```bash
set -a
source .env
set +a
uv run --locked python scripts/collect_buoy.py
```

기본 차단 동작 확인:

```bash
uv run --locked python scripts/predict_latest.py --trust-local-model
```

품질과 기압 의미가 미확인인 상태를 받아들이는 **학습·시연 전용** 실행:

```bash
uv run --locked python scripts/predict_latest.py \
  --trust-local-model --allow-unverified-source
```

Docker 수집기의 DB는 호스트 DB와 다르다. 현재 CLI 기본 경로는 호스트의
`data/runtime/buoy_observations.sqlite3`다. 볼륨 안 DB를 자동 복사하지 않는다.

외부 API 없는 회귀 테스트:

```bash
uv run --locked pytest -q
```

실제 API 테스트는 먼저 모델을 생성하고 인증키를 환경변수에 설정한 뒤 허용한다.
기존 수집 테스트 2개와 새 예측 테스트 1개에서 총 기본 4회 API 요청을 사용한다.
임시 DB만 사용하며 실제 API 장애·최근 자료의 누락에 따라 실패할 수 있다.

```bash
RUN_LIVE_BUOY_TESTS=1 uv run --locked pytest \
  tests/test_live_buoy_collection.py tests/test_live_prediction.py -q --tb=no
```

## 4. 이번 실행 기록

- 로컬 모델 ID: `rf-22101-0c2a284dede20560`.
- 학습 3,891개, 검증 1,130개.
- 검증 MAE 0.1928395169m, RMSE 0.2735833343m.
- 재로딩 전후 검증 예측 배열 정확히 일치.
- 최종 전체 테스트: 668개 수집, 665개 통과, 실제 API 전용 3개 건너뜀,
  실패 0개(11.91초). 단위 테스트와 로컬 SQLite·자식 프로세스·실제 과거 데이터
  통합 테스트를 포함하며 기본 실행은 실제 기상청 API를 호출하지 않는다.
- 저장된 실제 관측으로 실험 추론 성공:
  기준 2026-10-24 05:00 KST → 목표 11:00 KST.
  현재 파고 0.2m → 예측 약 0.1838428110m.
- 해당 결과는 예측이 실행됐다는 증거이며 11시 실제값과의 정확도 검증이 아니다.
  AQC 원문에 0 이외의 문자가 있어도 위치 해석이 안 된 상태이므로 정상으로 단정하지 않는다.

실제 API 전용 테스트를 별도로 허용해 실행: 3개 수집·통과, 실패 0개(3.82초).
새 예측 테스트는 실제 API → 임시 SQLite → 정규화 → 특성 생성 → 저장 모델 로딩
→ 실험 예측 전체를 통과했다. 이 테스트 역시 공식 QC 검증 완료를 뜻하지 않는다.

## 5. 면접에서 설명할 핵심

“학습과 실시간 입력이 같은 의미인지 확인하는 과정이 중요했습니다.
파고와 풍속의 결측은 0으로 대체하지 않고, 불규칙한 시각에는 행 이동 대신
정확한 시간 키로 과거 관측을 연결했습니다. 학습한 모델은 입력 순서와 환경 정보를
함께 저장했고, 다시 로딩해 예측이 같은지 검증했습니다. 공식 QC와 기압 항목의
의미가 미확인인 부분은 기본적으로 차단하고 실험 모드의 경고로 분리했습니다.”

현재는 관측소 한 곳의 6시간 뒤 유의파고를 예측하는 실험이다.
특정 해안 전체의 운항 위험, 향후 6시간 최대 파고, 선박별 안전성까지 예측하지 않는다.
2025년 검증 성능이 2026년 실시간 성능을 보장하지도 않는다.
