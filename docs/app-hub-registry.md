# Workspace App Registry

`shared.app_registry`는 Workspace app metadata의 유일한 public entry point입니다.
Portal과 Calendar는 `shared/apps.json`을 직접 읽지 않고 시작 시 생성된 동일
`AppRegistry` snapshot을 사용합니다.

```text
Portal / App Shell / Calendar
             |
      shared.app_registry
             |
       shared/apps.json
```

## Portal consumers

- Sidebar: `get_navigation()`
- Launcher: `get_launcher()`
- Home Cards와 `/api/hub/apps`: `get_enabled()`
- active app: `get_app()` 또는 `resolve_route()`
- 기본 pin: `get_default_pinned_ids()`

Sidebar, Launcher, Home 전용 app 배열은 없습니다. 각 surface는 같은 Registry snapshot에
대해 자신의 visibility query만 실행합니다. Home은 app manifest가 아니라 Workspace의 고정
navigation route입니다.

## Pinned apps

고정 앱은 기존 형식을 유지합니다.

```text
key: my-workspace:pinned-apps:v1
value: ["calendar", "notes"]
```

이름이나 URL이 아니라 app ID만 저장합니다. 손상된 값, 중복 ID, 삭제되거나 disabled 된 ID는
제거하고 저장 값이 없으면 `default_pinned` app IDs를 사용합니다. 따라서 display name이나
route가 바뀌어도 pin은 유지됩니다.

## Runtime boundary

Registry는 metadata와 Portal 표시 여부를 관리합니다. ASGI app factory import, mount path,
lifespan은 runtime composition의 책임입니다. 현재 Calendar mount는 `portal/main.py`에
명시적으로 남아 있으며 Registry metadata와 자동으로 결합하지 않습니다.

향후 자동 등록을 도입하려면 manifest source 외에 trusted `RuntimeProvider` 정책을 별도로
설계하고 승인해야 합니다.

전체 schema와 validation 규칙은 [App manifest schema](app-manifest-schema.md)를 참고하세요.
