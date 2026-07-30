# My Workspace

여러 내부 앱과 외부 바로가기를 한 곳에서 여는 로컬 FastAPI App Hub입니다.
기존 `todo-app`의 이력을 보존해 편입한 Calendar, 독립적인 Notes 문서 앱,
그리고 immutable snapshot 기반 Publishing Platform을 제공합니다.

## 구조

```text
apps/calendar/   Calendar FastAPI sub-application, template, 로컬 vendor 자산
apps/notes/      Notes CRUD, 검색, 논리적 source link, 전용 정적 자산
portal/          App Hub composition root, Home template와 전용 자산
shared/          App Shell, Registry, Markdown Core와 cross-app 공통 계약
publishing/      Publication lifecycle, type registry, viewers와 search
assets/          Workspace 공용 이미지 metadata, storage와 reference reconcile
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
- Notes: <http://127.0.0.1:8000/apps/notes/>
- Calendar API: `/apps/calendar/api/tasks`, `/apps/calendar/api/topics`
- Notes API: `/apps/notes/api/notes`
- Publications: <http://127.0.0.1:8000/publications/>
- Publication Type 관리: <http://127.0.0.1:8000/publications/types/>
- Calendar OpenAPI: `/apps/calendar/docs`

Calendar DB 기본 경로는 workspace 기준 `instance/calendar/tasks.db`입니다.
Notes DB 기본 경로는 `instance/notes/notes.db`이며 Calendar DB와 물리적으로 분리됩니다.
Publishing DB는 `instance/publishing/publishing.db`, Asset DB는
`instance/assets/assets.db`에 별도로 생성됩니다.
Publication Type 관리 화면의 선언형 유형은 전체 provider 검증 후 즉시
Registry snapshot에 반영되며 Workspace 재시작이 필요하지 않습니다.

## App Registry

Workspace 앱 metadata의 유일한 진입점은 `shared.app_registry`입니다.
`shared/apps.json`의 검증된 immutable snapshot 하나를 Portal과 내부 앱이 공유합니다.

- Sidebar: `AppRegistry.get_navigation()`
- Launcher: `AppRegistry.get_launcher()`
- Home Cards/API: `AppRegistry.get_enabled()`
- 기본 고정 앱: `AppRegistry.get_default_pinned_ids()`

앱 manifest schema, validation, route 정규화와 향후 discovery 확장 계약은
[App manifest schema](docs/app-manifest-schema.md)를 참고하세요. App metadata 등록과
FastAPI sub-application mount는 별도 책임이며 Calendar와 Notes mount는 Portal composition에
명시적으로 남아 있습니다.

```bash
CALENDAR_DB_PATH=/absolute/path/tasks.db python -m uvicorn portal.main:app
NOTES_DB_PATH=/absolute/path/notes.db python -m uvicorn portal.main:app
```

호환을 위해 `TODO_DB_PATH`도 지원하지만 `CALENDAR_DB_PATH`가 우선합니다. 운영 환경에서는 상대경로보다
절대경로를 권장합니다.

## 테스트

```bash
PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider tests

node --test tests/calendar/*.mjs tests/portal/*.mjs tests/notes/*.mjs
npm run test:markdown

PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python -m pytest -q tests/e2e
```

Codex filesystem sandbox 안에서는 AnyIO cross-thread blocking portal 제한 때문에 `TestClient`가
멈출 수 있습니다. 제품 코드 문제가 아니며, 재현과 판정 근거는
[`docs/pre-integration-validation.md`](docs/pre-integration-validation.md)에 있습니다.

## 로컬 자산 및 외부 링크

Calendar의 FullCalendar 7.0.2와 Pretendard 1.3.9는 기존 vendor 파일을 그대로 사용합니다.
Marked 18.0.7과 DOMPurify 3.4.12도 lockfile과 hash가 고정된 로컬 ESM 자산을 사용합니다.
Portal과 Calendar의 정상 로드/CRUD는 외부 host에 요청하지 않습니다. 외부 App Hub 항목은 로컬
메타데이터와 아이콘만 렌더링하며 사용자가 링크를 클릭할 때만 새 탭으로 이동합니다.

## App Shell

`shared/templates/app_shell.html`이 Sidebar, Header, 앱 launcher, 모바일 drawer와 main content
영역을 제공합니다. `portal/templates/home.html`과
`apps/calendar/app/templates/calendar.html`과 `apps/notes/app/templates/notes.html`은 같은
shell을 상속합니다. 두 앱은 FastAPI sub-application으로 명시적으로 mount되며 각 API/static
경로 계약을 유지합니다.

공통 디자인 토큰과 shell 레이아웃은 `shared/static/shell.css`, 공통 navigation·focus·sidebar
상태는 `shared/static/shell.js`에 있습니다. Calendar의 toolbar, grid, 일정 panel과 dialog
스타일은 Calendar 전용 stylesheet에 남아 있습니다.

## 운영 문서

- [편입 전 검증](docs/pre-integration-validation.md)
- [SQLite 백업과 복원](docs/database-backup-restore.md)
- [App Hub registry](docs/app-hub-registry.md)
- [Notes architecture](docs/notes-architecture.md)
- [Cross-app linking](docs/cross-app-linking.md)
- [Shared Markdown Core](docs/shared-markdown-core.md)
- [Publishing architecture](docs/publishing-architecture.md)
- [Publication Type 확장](docs/publication-type-extension.md)
- [Workspace 이미지와 Publishing](docs/publishing-images.md)
- [내부 앱 추가](docs/adding-an-internal-app.md)
- Calendar의 기존 기능·API·vendor 정보: [apps/calendar/README.md](apps/calendar/README.md)
