# My Workspace

여러 내부 앱과 외부 바로가기를 한 곳에서 여는 로컬 FastAPI App Hub입니다.
첫 번째 내부 앱은 기존 `todo-app`의 이력을 보존해 편입한 Calendar입니다.

## 구조

```text
apps/calendar/   Calendar FastAPI sub-application, template, 로컬 vendor 자산
portal/          App Hub composition root, Home template와 전용 자산
shared/          공통 App Shell template, design token, navigation과 registry
instance/        Git에서 제외되는 실행 데이터
tests/           Calendar 원본 회귀와 Portal 통합 테스트
docs/            편입·DB·registry 운영 문서
```

## 설치와 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python -m playwright install chromium
python -m uvicorn portal.main:app --reload
```

- App Hub: <http://127.0.0.1:8000/>
- Calendar: <http://127.0.0.1:8000/apps/calendar/>
- Calendar API: `/apps/calendar/api/tasks`, `/apps/calendar/api/topics`
- Calendar OpenAPI: `/apps/calendar/docs`

Calendar DB 기본 경로는 workspace 기준 `instance/calendar/tasks.db`입니다.

## App Registry

Workspace 앱 metadata의 유일한 진입점은 `shared.app_registry`입니다.
`shared/apps.json`의 검증된 immutable snapshot 하나를 Portal과 내부 앱이 공유합니다.

- Sidebar: `AppRegistry.get_navigation()`
- Launcher: `AppRegistry.get_launcher()`
- Home Cards/API: `AppRegistry.get_enabled()`
- 기본 고정 앱: `AppRegistry.get_default_pinned_ids()`

앱 manifest schema, validation, route 정규화와 향후 discovery 확장 계약은
[App manifest schema](docs/app-manifest-schema.md)를 참고하세요. App metadata 등록과
FastAPI sub-application mount는 별도 책임이며 현재 Calendar mount는 Portal composition에
명시적으로 남아 있습니다.

```bash
CALENDAR_DB_PATH=/absolute/path/tasks.db python -m uvicorn portal.main:app
```

호환을 위해 `TODO_DB_PATH`도 지원하지만 `CALENDAR_DB_PATH`가 우선합니다. 운영 환경에서는 상대경로보다
절대경로를 권장합니다.

## 테스트

```bash
PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider \
  tests/calendar tests/portal tests/integration

node --test tests/calendar/*.mjs tests/portal/*.mjs

PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python -m pytest -q tests/e2e
```

Codex filesystem sandbox 안에서는 AnyIO cross-thread blocking portal 제한 때문에 `TestClient`가
멈출 수 있습니다. 제품 코드 문제가 아니며, 재현과 판정 근거는
[`docs/pre-integration-validation.md`](docs/pre-integration-validation.md)에 있습니다.

## 로컬 자산 및 외부 링크

Calendar의 FullCalendar 7.0.2와 Pretendard 1.3.9는 기존 vendor 파일을 그대로 사용합니다.
Portal과 Calendar의 정상 로드/CRUD는 외부 host에 요청하지 않습니다. 외부 App Hub 항목은 로컬
메타데이터와 아이콘만 렌더링하며 사용자가 링크를 클릭할 때만 새 탭으로 이동합니다.

## App Shell

`shared/templates/app_shell.html`이 Sidebar, Header, 앱 launcher, 모바일 drawer와 main content
영역을 제공합니다. `portal/templates/home.html`과
`apps/calendar/app/templates/calendar.html`은 같은 shell을 상속합니다. Calendar는 계속
FastAPI sub-application으로 mount되므로 `/apps/calendar/api/*`와
`/apps/calendar/static/*` 계약은 유지됩니다.

공통 디자인 토큰과 shell 레이아웃은 `shared/static/shell.css`, 공통 navigation·focus·sidebar
상태는 `shared/static/shell.js`에 있습니다. Calendar의 toolbar, grid, 일정 panel과 dialog
스타일은 Calendar 전용 stylesheet에 남아 있습니다.

## 운영 문서

- [편입 전 검증](docs/pre-integration-validation.md)
- [SQLite 백업과 복원](docs/database-backup-restore.md)
- [App Hub registry](docs/app-hub-registry.md)
- Calendar의 기존 기능·API·vendor 정보: [apps/calendar/README.md](apps/calendar/README.md)
