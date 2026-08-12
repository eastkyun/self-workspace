# My Workspace

My Workspace는 Calendar, Notes, Publishing을 하나의 인증·권한·검색 경험으로 묶은
로컬 우선 FastAPI 워크스페이스 플랫폼입니다. 각 도메인의 SQLite 데이터베이스는
분리하면서도 공통 App Shell, 사용자 상태, 내부 링크, 이미지 자산과 활동 기록을
공유합니다.

## 주요 기능

- **Identity와 Workspace**: 회원가입·로그인·로그아웃, 세션, 비밀번호 재설정,
  멀티 워크스페이스 선택과 멤버 초대
- **권한 관리**: Owner/Admin/Member/Viewer 역할, 앱별 접근 정책과 멤버별 권한
  override
- **Workspace Home**: 앱 바로가기, 사용자별 Dashboard 배치, 즐겨찾기, 최근 본
  항목과 활동 기록
- **통합 검색**: 권한과 활성 앱 정책을 적용한 Notes·Calendar·Publishing 검색
- **Calendar**: 일정·주제 관리, Markdown 상세 내용, Notes 연결과 회의 노트 생성
- **Notes**: 문서 CRUD, 검색, 휴지통 복구, 내부 링크, 이미지 붙여넣기와 Publishing
- **Publishing**: immutable version snapshot, 공개 뷰어, 복원·보관, Publication Type
  관리
- **공통 UX**: 반응형 Sidebar/App Launcher, 계정 메뉴, 접근 가능한 SVG 로딩 상태,
  reduced-motion 지원
- **로컬 자산**: FullCalendar, Marked, DOMPurify를 외부 CDN 없이 제공

## 기술 구성

- Python 3.12+
- FastAPI / Starlette / Uvicorn
- SQLite — Identity, Calendar, Notes, Publishing, Assets 도메인별 분리
- Vanilla JavaScript ES modules
- FullCalendar 7.0.2, Marked 18.0.7, DOMPurify 3.4.12
- Pytest, Node test runner, Playwright

## 프로젝트 구조

```text
access/             권한 평가와 Workspace 접근 계약
identity/           인증, 세션, 사용자·Workspace·멤버십 관리
workspace_platform/ 앱 정책, Dashboard, 검색, 사용자 상태와 활동
apps/calendar/      Calendar UI/API, repository와 service
apps/notes/         Notes UI/API, repository와 service
publishing/         발행 lifecycle, type registry, 공개 viewer와 search
assets/             공용 이미지 metadata, binary storage와 reference reconcile
portal/             FastAPI composition root와 App Hub
shared/             App Registry, App Shell, Markdown Core, 설정과 migration 계약
tools/              Bootstrap 및 staging migration CLI
tests/              단위·통합·브라우저 회귀 테스트
docs/               아키텍처, 운영, 보안 및 검증 문서
instance/           로컬 실행 데이터; Git에서 제외
```

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci

PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python -m playwright install chromium
```

## 실행

기본 개발 설정은 코드에 포함되어 있습니다. 설정을 명시하려면 `.env.example`을
참고해 환경 변수를 export하세요. 애플리케이션이 `.env` 파일을 자동으로 읽는다고
가정하지 않습니다.

```bash
./start.sh
```

또는 직접 실행할 수 있습니다.

```bash
.venv/bin/python -m uvicorn portal.main:app --reload
```

브라우저에서 <http://127.0.0.1:8000/>을 열고 회원가입합니다. 회원가입 시 첫
Workspace가 함께 생성되며, 로그인 후 Workspace 선택 화면을 거쳐 정식 경로로
이동합니다.

```text
/login
/signup
/workspace-selector
/w/{workspace_id}/home
/w/{workspace_id}/calendar
/w/{workspace_id}/notes
/w/{workspace_id}/publications
/w/{workspace_id}/publications/types
/w/{workspace_id}/search
/w/{workspace_id}/settings/*
```

`/apps/calendar/`, `/apps/notes/`, `/apps/publishing/` 등 기존 경로는 호환 진입점이며,
새 UI와 API 연동에는 Workspace 범위가 명시된 `/w/...` 및
`/api/v1/workspaces/{workspace_id}/...` 경로를 사용합니다.

## 설정

전체 예시는 [`.env.example`](.env.example)에 있습니다.

| 변수 | 개발 기본값 | 설명 |
| --- | --- | --- |
| `APP_ENV` | `development` | `production`에서는 HTTPS origin과 secure cookie를 강제합니다. |
| `PUBLIC_ORIGIN` | `http://127.0.0.1:8000` | 링크·보안 검증에 사용하는 공개 origin |
| `IDENTITY_DB_PATH` | `instance/identity/identity.db` | 사용자, 세션, Workspace와 권한 DB |
| `CALENDAR_DB_PATH` | `instance/calendar/tasks.db` | Calendar DB |
| `NOTES_DB_PATH` | `instance/notes/notes.db` | Notes DB |
| `PUBLISHING_DB_PATH` | `instance/publishing/publishing.db` | Publishing DB |
| `ASSET_DB_PATH` | `instance/assets/assets.db` | Asset metadata DB |
| `PUBLICATION_TYPES_PATH` | `instance/publishing/types.json` | 선언형 Publication Type 저장소 |
| `SESSION_COOKIE_SECURE` | `false` | 운영 환경에서는 반드시 `true` |
| `TRUSTED_PROXIES` | 비어 있음 | 신뢰할 reverse proxy IP/CIDR 목록 |

`TODO_DB_PATH`는 Calendar DB의 레거시 호환 변수이며 `CALENDAR_DB_PATH`가 우선합니다.
운영 환경에서는 DB 경로를 절대경로로 지정하는 것을 권장합니다.

## 기존 데이터 Bootstrap과 Migration

신규 개발 환경은 웹 회원가입으로 시작할 수 있습니다. 기존 도메인 DB를 새로운
Identity/Workspace 모델에 연결할 때는 서비스를 중지하고 single-worker 조건을
명시한 뒤 bootstrap CLI를 사용합니다. 비밀번호는 명령행 인자로 받지 않고 TTY에서
안전하게 입력합니다.

```bash
export WORKSPACE_SINGLE_WORKER_ASSERTED=true
.venv/bin/python -m tools.workspace_cli bootstrap-owner --email owner@example.com
```

Staging migration은 production을 직접 변경하지 않습니다.

```bash
.venv/bin/python -m tools.workspace_cli stage-domains
.venv/bin/python -m tools.workspace_cli verify-staging
.venv/bin/python -m tools.workspace_cli prepare-cutover
```

실제 cutover 전에는 [Workspace migration contract](docs/workspace-migration-contract.md)와
[SQLite 백업·복원](docs/database-backup-restore.md)을 확인하세요.

## 성능과 프런트엔드 자산

- 응답 본문은 Starlette GZip middleware로 압축합니다.
- Calendar는 필요한 day-grid 기능과 한국어 locale만 포함한 로컬 ESM bundle을
  사용합니다.
- 초기 데이터가 필요한 앱은 공통 SVG loader를 데이터 정착 시점까지 유지하고,
  서버 렌더링 화면은 첫 paint 직후 해제합니다.
- App Shell의 계정 요청은 Hub와 공유해 중복 호출을 피합니다.
- Calendar 이벤트 갱신은 batch rendering으로 불필요한 전체 render를 줄입니다.
- 모든 핵심 화면과 vendor 자산은 정상 동작 중 외부 host를 요청하지 않습니다.

Vendor 갱신 후에는 lockfile과 manifest/hash를 함께 검증하세요.

```bash
npm run verify:markdown-vendor
```

## 테스트

Python 전체 테스트:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests
```

JavaScript와 Markdown Core:

```bash
node --test tests/calendar/*.mjs tests/notes/*.mjs tests/portal/*.mjs tests/publishing/*.mjs
npm run test:markdown
```

Chromium E2E:

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/e2e
```

## 설계 원칙

- 각 도메인은 별도 DB와 repository/service 경계를 유지합니다.
- 모든 Workspace API는 인증된 Workspace context와 권한 검사를 거칩니다.
- App metadata의 단일 진입점은 `shared.app_registry`와 `shared/apps.json`입니다.
- 공통 Markdown은 입력을 신뢰하지 않고 DOMPurify allowlist를 통과시킵니다.
- 발행본은 수정 가능한 원문과 분리된 immutable snapshot입니다.
- 이미지 metadata는 Asset DB에, binary는 전용 파일 저장소에 분리해 관리합니다.

## 문서

- [Workspace identity와 tenancy](docs/workspace-identity-tenancy-baseline.md)
- [Authorization threat model](docs/workspace-authorization-threat-model.md)
- [Workspace migration contract](docs/workspace-migration-contract.md)
- [App manifest schema](docs/app-manifest-schema.md)
- [App Hub registry](docs/app-hub-registry.md)
- [내부 앱 추가](docs/adding-an-internal-app.md)
- [Notes architecture](docs/notes-architecture.md)
- [Cross-app linking](docs/cross-app-linking.md)
- [Shared Markdown Core](docs/shared-markdown-core.md)
- [Publishing architecture](docs/publishing-architecture.md)
- [Publication Type 확장](docs/publication-type-extension.md)
- [Workspace 이미지와 Publishing](docs/publishing-images.md)
- [SQLite 백업과 복원](docs/database-backup-restore.md)
