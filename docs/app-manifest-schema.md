# App Manifest Schema

## Registry document

Registry data는 `shared/apps.json`에 저장합니다.

```json
{
  "schema_version": 1,
  "categories": [],
  "apps": []
}
```

지원하는 `schema_version`은 현재 `1`뿐입니다. 누락되거나 지원 범위를 벗어난 버전은
`unsupported_schema_version` 오류로 startup 전에 거부됩니다. Registry JSON은 category와
app을 결정적인 순서로 작성하고 Git review 대상으로 관리합니다.

## Category

```json
{
  "id": "productivity",
  "display_name": "Productivity",
  "order": 10,
  "description": "Planning, scheduling, and daily work."
}
```

Category는 JSON document에서 선언하며 code enum으로 고정하지 않습니다. Category 결과는
`order`, `display_name`, `id` 순으로 정렬됩니다.

## App manifest

```json
{
  "id": "calendar",
  "name": "calendar",
  "display_name": "Calendar",
  "description": "월간 캘린더에서 일정과 주제를 관리합니다.",
  "icon": "/static/icons/calendar.svg",
  "route": "/apps/calendar/",
  "category": "productivity",
  "enabled": true,
  "default_pinned": true,
  "navigation_visible": true,
  "launcher_visible": true,
  "order": 10,
  "version": "1.0.0",
  "kind": "internal",
  "search_keywords": ["schedule", "tasks", "일정"]
}
```

필드:

- `id`: Registry lookup과 localStorage pin의 불변 식별자
- `name`: 기술적 slug
- `display_name`: 사용자 표시명
- `description`: 최대 160자 설명
- `icon`: `/static/icons/` 아래 실제 로컬 파일
- `route`: 내부 Workspace route 또는 외부 HTTPS URL
- `category`: 선언된 category ID
- `enabled`: 모든 Portal surface의 최상위 사용 가능 여부
- `default_pinned`: 저장 값이 없을 때 기본 고정 여부
- `navigation_visible`: Sidebar 표시 여부
- `launcher_visible`: Launcher 표시 여부
- `order`: 같은 category 안에서의 정렬 값
- `version`: `MAJOR.MINOR.PATCH`
- `kind`: `internal` 또는 `external`
- `search_keywords`: 검색 보조어

disabled app은 navigation, launcher, Home Cards/API, default pin과 route resolution에서 모두
제외됩니다.

## Route policy

내부 route는 `/apps/`로 시작하고 query, fragment, 빈 segment, `.` 또는 `..` segment를
허용하지 않습니다. 저장 시 trailing slash 하나가 붙은 형태로 정규화합니다.

```text
/apps/calendar  -> /apps/calendar/
/apps/calendar/ -> /apps/calendar/
```

따라서 위 두 값은 중복 route입니다.

`resolve_route()`는 문자열 prefix가 아닌 path segment 경계를 검사합니다.

```text
/apps/calendar/api/tasks -> calendar
/apps/calendarish        -> no match
/apps/cal                -> no match
```

외부 app route는 예외 없이 `https://`만 허용합니다. localhost나 개발용 HTTP 예외는
제공하지 않습니다.

## Search policy

`search()`는 다음 규칙을 사용합니다.

1. query의 앞뒤 공백 제거
2. 연속 whitespace를 한 칸으로 정규화
3. Unicode `casefold()`로 대소문자 비구분
4. ID, name, display name, description, keywords의 substring 검색
5. 빈 query는 `get_enabled()`와 동일
6. 기본적으로 disabled app 제외

fuzzy ranking이나 외부 검색 dependency는 사용하지 않습니다.

## Validation

Startup 전에 가능한 오류를 수집해 한 `RegistryValidationError`로 보고합니다.

- 필수/unknown field
- 지원하지 않는 schema version
- 중복 app ID와 정규화된 route
- 중복 category ID와 category order
- 같은 category 안의 app order 충돌
- 선언되지 않은 category
- 누락된 local icon
- 잘못된 internal route 또는 HTTP external route
- 잘못된 semantic version
- disabled app의 `default_pinned=true`

오류는 source, app ID, field, code, message 순으로 정렬되어 로그와 테스트 결과가
결정적입니다.

## Public API

```python
AppRegistry.load_default()
AppRegistry.from_source(source)
AppRegistry.from_sources(sources)

registry.get_all()
registry.get_enabled()
registry.get_navigation()
registry.get_launcher()
registry.get_app(app_id)
registry.exists(app_id)
registry.categories()
registry.search(query)
registry.resolve_route(path)
registry.get_default_pinned_ids()
```

Collection query는 immutable tuple을 반환합니다. App와 category model도 frozen입니다.

## Future manifest and plugin sources

현재 구현된 source는 `JsonRegistrySource`뿐이며 자동 filesystem discovery나 plugin import는
수행하지 않습니다. `RegistrySource` protocol이 향후 다음 source의 확장점입니다.

- `ManifestDirectorySource`: `apps/*/manifest.json`
- `PythonManifestSource`: trusted built-in Python manifest
- `PluginEntryPointSource`: 설치된 package entry point

이 이름들은 확장 방향을 설명하기 위한 계약이며 현재 placeholder class로 구현하지 않습니다.
외부 code 실행과 ASGI mount는 별도 `RuntimeProvider` 보안·lifespan 정책이 승인된 이후에만
도입합니다.
