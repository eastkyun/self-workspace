# Calendar 편입 전 검증 기록

검증일: 2026-07-28 (Asia/Seoul)

## 목적

`/home/eastvirus/projects/todo-app`을 변경하지 않은 상태에서 Python API 테스트 timeout이
다음 중 어디에서 발생하는지 분리했다.

- 테스트 실행 환경
- FastAPI lifespan
- SQLite 잠금
- Starlette/FastAPI `TestClient`
- 네트워크 대기

## 원본 불변 기준선

- Git branch: `master`
- Git HEAD: `fb6d073777e39af877b246ecfd3170c119ab3352`
- Git status: clean
- `instance/tasks.db`
  - size: 40960 bytes
  - SHA-256: `82e4c05b6ff5a4609cb98215c13a1d16d679b8d9c45d7c22a6e6bde6161de39c`
- `instance/tasks.db.pre-calendar-v0.bak`
  - size: 16384 bytes
  - SHA-256: `f3ab0b703c4467509c54d369cfce0e7fc44c425d7339347e664a296469359a60`
- `instance/tasks.db.pre-topics-v1.bak`
  - size: 20480 bytes
  - SHA-256: `ab1b0fbbf4fd7012f3a7427349588b0c9539a32bd22270cab3a5d1755881275f`
- 검증 전후 DB mtime/inode/checksum 동일
- 검증 전후 `-wal`, `-shm` sidecar 없음

테스트 실행 시 원본 저장소에 pytest cache와 Python bytecode를 만들지 않도록
`-p no:cacheprovider`와 `PYTHONDONTWRITEBYTECODE=1`을 사용했다. 모든 DB 테스트는 기존 fixture의
임시 SQLite 경로를 사용했다.

## 환경

```text
Python 3.12.3
Node 22.23.1
fastapi 0.115.14
starlette 0.46.2
httpx 0.28.1
anyio 4.8.0
pytest 8.4.2
pydantic 2.13.4
```

## 재현 절차

### 1. 기존 API 테스트의 정지 위치

```bash
cd /home/eastvirus/projects/todo-app
PYTHONDONTWRITEBYTECODE=1 \
timeout --signal=INT --kill-after=5s 90s \
.venv/bin/python -X faulthandler -m pytest \
  -p no:cacheprovider -vv -s tests/test_api.py
```

결과:

- 35개 API 테스트 수집 완료
- 첫 테스트 `test_empty_database_returns_empty_list`의 fixture 진입 중 정지
- 90초 timeout 후 종료

### 2. 앱/DB 단계 분리

각 사례는 새 임시 디렉터리의 `x.db`를 사용했다.

| 사례 | 샌드박스 결과 |
|---|---|
| `initialize_database(temp_db)` 단독 | 즉시 성공 |
| `create_app(temp_db)` 단독 | 즉시 성공 |
| `TestClient(app)` 생성만 | 즉시 성공 |
| `TestClient.__enter__()` | timeout |
| lifespan 없는 최소 `FastAPI()` + `TestClient.__enter__()` | 동일 timeout |

따라서 Calendar의 lifespan 코드와 SQLite 초기화/잠금은 정지의 필요조건이 아니다.

### 3. AnyIO 최소 재현

```python
from anyio.from_thread import start_blocking_portal

context = start_blocking_portal()
portal = context.__enter__()
print(portal.call(lambda: 42))
context.__exit__(None, None, None)
```

Codex filesystem/process sandbox 안에서는 portal thread가 시작된 뒤 `portal.call()`이 완료되지 않았다.
faulthandler stack은 다음 대기 관계를 보였다.

- portal thread: asyncio selector의 `select()` 대기
- caller thread: AnyIO `_spawn_task_from_thread()`의 `Future.result()` 대기

동일 명령을 sandbox 밖의 승인된 실행으로 재시도했을 때 출력은 다음과 같았다.

```text
before
entered
42
exited
```

### 4. 기존 전체 Python 회귀

sandbox 밖에서 캐시/바이트코드를 끄고 실행:

```bash
cd /home/eastvirus/projects/todo-app
PYTHONDONTWRITEBYTECODE=1 \
timeout --signal=TERM --kill-after=5s 180s \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_api.py tests/test_migration.py
```

결과:

```text
45 passed in 4.74s
```

### 5. 기존 JavaScript 회귀

```bash
node --test tests/test_calendar.mjs
```

결과:

```text
1 suite passed
```

## 원인 판정

### 직접 원인

현재 Codex sandbox 안에서 AnyIO의 cross-thread blocking portal 호출이 깨어나지 않는다.
Starlette `TestClient`는 이 portal을 사용하므로 context 진입과 in-process 요청이 멈춘다.

### 제외된 원인

- **서버 lifespan:** lifespan이 전혀 없는 최소 FastAPI 앱도 같은 위치에서 멈췄다.
- **DB 잠금/초기화:** 임시 DB의 `initialize_database()` 단독 실행이 즉시 성공했다.
- **기존 운영 DB:** 테스트 fixture는 임시 DB를 사용하며 운영 DB checksum/mtime/inode가 변하지 않았다.
- **네트워크 대기:** 외부 소켓을 사용하지 않는 최소 in-process FastAPI route와 bare AnyIO portal도
  동일하게 멈췄다.
- **기존 TestClient/API 코드 결함:** 동일 환경과 기존 코드가 sandbox 밖에서 45개 테스트를 모두 통과했다.

## 최소 수정 판단

기존 `todo-app` 소스 또는 테스트에는 timeout 해결을 위한 수정이 필요하지 않다.
AnyIO/FastAPI/Starlette version pin 변경도 현재 증거로는 정당화되지 않는다.

통합 후 Python TestClient 회귀는 sandbox 밖의 승인된 실행으로 수행한다. sandbox 안에서는
TestClient timeout을 제품 회귀로 판정하지 않으며, 순수 DB/함수/Node 테스트만 정상적으로 실행한다.

## 편입 진행 게이트

- 원인이 테스트 실행 환경으로 분리됨: 통과
- 기존 Python 45개 회귀: 통과
- 기존 Node 회귀: 통과
- 원본 Git/DB 불변성: 통과

따라서 다음 단계인 Git bundle 및 subtree import 명령 검토로 진행할 수 있다. 실제 명령은 실행 전에
사용자에게 제시하고 별도 승인을 받는다.
