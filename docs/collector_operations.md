# 부이 수집기 실행과 운영

## 1. 범위와 현재 구조

대상은 지점 22101의 관측 수집이다. 예측 API나 운항 참고 등급은 포함하지 않는다.

```text
run_buoy_collector.py: 반복·프로세스 제한시간·중복 실행 잠금
→ collect_buoy.py: API 조회·저장·재조회 대조
→ buoy_client.py: HTTP 통신과 응답 파서 연결
→ buoy_store.py: SQLite 최신 수신 버전 저장
```

수집 설정은 기본 최근 12시간, 완료 후 600초 대기, 자식 실행 제한 60초,
연속 실패 한계 3회다. API 시각은 KST로 요청하고 관측은 UTC로 저장한다.
최신 12시간을 중복 조회하여 전송 지연을 따라잡지만, 12시간보다 긴 중단의
누락을 자동 복구하는 기능은 아직 없다. 실제 관측 간격이나 완전성을 보장하지 않는다.

이번 저장소는 수정 전 전체 버전 이력이 아니라 관측소·시각별 최신 수신값을 유지한다.
`processed_count`는 새로 삽입한 행 수가 아닌 처리한 입력 수다.
결측·QC·기압 항목의 의미 미확인 부분은 그대로 남아 있으며 원문을 보존한다.

## 2. 로컬 1회·유한 횟수 실행

프로젝트 루트에서 본인이 작성한 단순 환경변수 파일만 불러온다.
`source`는 파일을 셸 코드로 실행하므로 모르는 파일에는 사용하지 않는다.
인증키는 명령행 인자에 넣거나 출력하지 않는다.

```bash
uv sync --locked
set -a
source .env
set +a
uv run --locked python scripts/collect_buoy.py
uv run --locked python scripts/run_buoy_collector.py --runs 2 --interval-seconds 10
```

기본 로컬 DB는 `data/runtime/buoy_observations.sqlite3`다.
상태는 `buoy_observations.collector.json`, 잠금은 `buoy_observations.collector.lock`이다.
이 디렉터리는 Git에서 제외한다. Git 제외는 백업이 아니다.

## 3. 반복 실행 동작

```bash
uv run --locked python scripts/run_buoy_collector.py
```

- 한 실행기 안에서는 자식이 종료되기 전 다음 수집을 시작하지 않는다.
- 같은 DB 경로의 실행기는 POSIX 파일 잠금으로 중복 실행을 거부한다.
- 잠금 파일이 남아 있어도 실행 중이라는 뜻은 아니다. 실제 잠금은 종료 시 해제된다.
  잠금을 우회하려고 파일을 지우지 않는다.
- 잠금은 macOS/Linux의 로컬 파일시스템을 전제로 한다. Windows 및 네트워크
  공유 파일시스템을 검증하지 않았다.
- 한 번 실패하면 다음 주기까지 기다리고, 성공하면 연속 실패 횟수를 0으로 돌린다.
- 연속 3회 실패하면 종료 코드 1로 나간다. 상주 환경은 관리자가 재시작한다.
- 유한 실행은 중간 실패 후 회복해도 종료 코드 1이다.
- Ctrl+C와 SIGTERM은 정리 경로로 종료하며 종료 코드는 130이다.
- timeout 발생 시 자식이 이미 DB commit을 끝냈을 수 있다. 다음 실행의 중복 방지로
  재수집을 허용하며, 정확히 한 번 실행을 보장하는 설계는 아니다.

600초는 시작 간격이 아니라 **직전 실행 종료 후 대기 시간**이다.
60초는 자식 프로세스 대기 제한이며 운영체제의 프로세스 생성 시간까지 엄밀히
포함하는 실시간 보장은 아니다. 긴 요청을 겹쳐 시작하지 않는다.

## 4. 상태 점검의 의미

```bash
uv run --locked python scripts/check_buoy_collector.py \
  --status-file data/runtime/buoy_observations.collector.json
```

상태 JSON은 같은 디렉터리의 임시 파일을 교체해 작성한다. 인증키나 요청 URL은
넣지 않는다. PID 생존, 실행 중 상태와 최근 성공시각을 확인하며 기본 허용 나이는
2100초다. 실패 상태여도 최근 성공이 있고 프로세스가 살아 있으면 일시적으로 정상일
수 있다. 최초 성공 전·종료·연속 실패 한계·오래된 성공·깨진 상태 파일은 비정상이다.

이는 **수집 프로세스 상태**다. 최신 관측 자체의 지연, 결측, QC 정상 여부나 모델
입력 가용성을 검사한 것이 아니다. 배포 상태 파일은 PID가 같은 컨테이너 안에서
해석되어야 한다. 호스트용과 컨테이너용 상태 파일을 섞지 않는다.

## 5. Docker Compose 단일 서버 배포

Docker Engine과 Compose가 실행 가능한 서버가 필요하다. 현재 저장소는
`Dockerfile.collector`, `.dockerignore`, `compose.collector.yaml`을 제공한다.
`.env`와 로컬 DB는 빌드 컨텍스트에 포함하지 않으며 키는 실행 시에만 주입한다.
이미지는 비루트 UID 10001로 실행하고 DB·잠금·상태만 영구 볼륨에 저장한다.

먼저 `.env.example`의 형식에 따라 서버의 `.env`에 API 키를 설정한다.
`.env` 파일 권한은 소유자만 읽도록 관리한다. Docker 관리자 권한이 있는 사용자는
컨테이너 환경변수를 볼 수 있으므로 서버 접근 권한도 관리해야 한다.

```bash
docker compose -f compose.collector.yaml config --quiet
docker compose -f compose.collector.yaml build
docker compose -f compose.collector.yaml up -d
docker compose -f compose.collector.yaml ps
docker compose -f compose.collector.yaml logs --tail 50 collector
```

`config`의 전체 출력에는 치환된 인증키가 들어갈 수 있으므로 공유하거나
로그에 남기지 않는다. 구문 검사는 `--quiet`로 한다.

운영 정책:

- `restart: unless-stopped`: 컨테이너 종료 시 재시작한다. 사용자가 명시적으로
  중지한 컨테이너는 자동 재시작하지 않는다.
- healthcheck가 `unhealthy`가 되는 것만으로 Compose가 재시작하지는 않는다.
  연속 수집 실패 한계로 **프로세스가 종료되는 경로**를 별도로 구현했다.
- 엔진 자체가 호스트 부팅 시 시작되도록 서버의 Docker 서비스도 구성해야 한다.
  Docker 엔진 설치·부팅 설정이나 원격 서버 배포를 자동 수행한 것은 아니다.
- 로컬 Mac이 잠자기 상태이거나 Docker Desktop이 꺼져 있으면 상시 수집되지 않는다.
- API/FastAPI worker 안에서 수집기를 시작하지 않는다. 수집 서비스는 단독 실행한다.
- 이름 있는 `buoy-data` 볼륨은 컨테이너를 재생성해도 유지된다.
  Compose 프로젝트 이름이 달라지면 다른 볼륨을 볼 수 있으므로 운영 이름을 유지한다.
- 일반 `docker compose down`은 이름 있는 볼륨을 지우지 않는다.
  **`down -v`는 관측 DB를 지우므로 실행하지 않는다.**
- 호스트의 기존 DB가 컨테이너 볼륨에 자동 복사되는 것은 아니다.
  컨테이너는 최근 관측부터 자체 수집한다.

명시적 중지·다시 시작:

```bash
docker compose -f compose.collector.yaml stop collector
docker compose -f compose.collector.yaml start collector
```

DB 백업은 SQLite의 일관된 백업 API를 사용하거나 수집기를 중지한 후 수행한다.
DB만 복사하면서 저널 파일을 무시하는 운영 절차는 사용하지 않는다.
현재 백업 자동화와 다른 서버로의 복구 검증은 미구현이다.

## 6. 검증 명령과 한계

기본 회귀 테스트는 실제 기상청 API를 호출하지 않는다. 실제 자식 프로세스,
SQLite 저장 지속성, timeout 후 자식 종료, SIGTERM 정리, 잠금 해제는 합성 입력으로
검증한다. Compose 구문 테스트는 가짜 키를 사용하며 엔진 실행을 요구하지 않는다.

```bash
uv run --locked pytest -q
```

실제 API 통합 테스트는 키를 환경변수에 설정한 뒤 명시적으로 허용한다.
임시 DB를 사용하고, 테스트 두 개에서 기본 총 3회 API 요청을 보낸다.

```bash
RUN_LIVE_BUOY_TESTS=1 uv run --locked pytest tests/test_live_buoy_collection.py -q --tb=no
```

이 테스트는 단위 테스트와 다르게 외부 장애나 관측 가용성 때문에 실패할 수 있다.
`--tb=no`는 실패 시 테스트 프레임에 인증키가 포함된 지역 변수가 노출되는 위험을
줄이기 위한 것이다. 운영 환경에서도 환경변수·지역 변수 전체를 출력하지 않는다.

Docker 설정의 구문 통과는 이미지 빌드·컨테이너 실행·재시작을 검증했다는 뜻이 아니다.
원격 서버 접속 대상이 없는 상태에서는 실제 서버 상주 운영 완료로 보고하지 않는다.

## 7. 2026-10-24 호스트 검증 기록

- 기본 회귀: 602개 수집, 600개 통과, 실제 API 전용 2개 건너뜀.
- 실제 API 전용 테스트를 별도로 허용해 실행: 2개 통과, 0개 실패.
- runtime DB 대상으로 실제 유한 수집 2회: 두 실행 모두 종료 코드 0.
- 각 실행 24건 수신·처리, 최종 DB 24행·고유 키 24개로 중복 없음 확인.
- 최종 관측시각: 2026-10-24 05:00 KST.
- 유한 실행 종료 후 상태 파일은 `stopped`이며 백그라운드 수집을 남기지 않음.

위 숫자는 해당 실행 시점의 기록이며 현재 데이터 가용성을 보장하지 않는다.
Docker 및 서버 검증은 이 호스트 테스트와 구분해 기록한다.

## 8. 2026-10-24 로컬 Docker 검증 기록

검증용 Compose 프로젝트 이름은 `marine-wave-collector-check`다.
기존 서비스와 구분된 컨테이너·볼륨으로 실행했다.

- Docker Engine 28.0.1에서 `Dockerfile.collector` 이미지 빌드 성공.
- 컨테이너에서 실제 API 유한 수집 2회 성공: 각각 24건 수신·저장 대조.
- 상주 실행 후 컨테이너 내부 상태 점검 명령이 정상으로 반환됨.
- 실행 사용자는 `collector`, 루트 파일시스템은 읽기 전용,
  재시작 정책은 `unless-stopped`임을 실제 컨테이너에서 확인.
- 수집 프로세스에 SIGKILL을 보내 장애를 발생시킨 뒤 Docker의
  `RestartCount=1`, 실행 상태 복구와 상태 점검 정상 반환을 확인.
- 장애 전후 SQLite는 전체 24행·고유 관측 키 24개를 유지.
- 컨테이너를 제거한 뒤 새 컨테이너에서 같은 볼륨을 열어 24행 유지 확인.
- 이미지 안에 `/app/.env`가 없음을 확인.
- 검증용 컨테이너·네트워크는 정리했고, 검증용 DB 볼륨
  `marine-wave-collector-check_buoy-data`는 복구 가능하도록 보존.

처음 `docker kill`로 컨테이너를 명시적으로 종료한 시도에서는 제한 시간 안에
자동 회복되지 않았다. 이 결과를 프로세스 장애 복구 성공으로 계산하지 않고,
컨테이너 안의 수집 프로세스만 종료하는 별도 시나리오로 다시 검증했다.
Docker의 명시적 중지와 애플리케이션 프로세스 장애는 구분해야 한다.

위 상태 점검은 Compose에 등록된 점검 명령을 컨테이너 안에서 직접 실행한 결과다.
Docker의 주기적 health 상태 전환, 장기간 무중단 운전, 호스트 재부팅,
원격 서버 배포 및 자동 백업·복구까지 검증한 것은 아니다.
검증 종료 시 상시 실행 중인 수집 컨테이너는 남기지 않았다.
