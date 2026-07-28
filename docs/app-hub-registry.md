# App Hub registry

앱은 `portal/apps.json`에 등록합니다. registry 변경은 Git review 대상이며 웹 UI 기반 관리 API는
제공하지 않습니다.

## 내부 앱

```json
{
  "id": "calendar",
  "name": "Calendar",
  "kind": "internal",
  "url": "/apps/calendar/",
  "icon": "/static/icons/calendar.svg",
  "description": "월간 캘린더에서 일정과 주제를 관리합니다.",
  "defaultPinned": true,
  "order": 10
}
```

- `id`: 소문자로 시작하는 영문 소문자·숫자·하이픈 조합, registry 내 고유값
- `kind=internal`: URL은 `/apps/`로 시작
- `icon`: `portal/static/icons/`에 실제로 존재하는 로컬 SVG
- `defaultPinned`: 저장된 사용자 설정이 없을 때 기본 고정 여부

## 외부 바로가기

```json
{
  "id": "example",
  "name": "Example",
  "kind": "external",
  "url": "https://example.com/",
  "icon": "/static/icons/example.svg",
  "description": "외부 서비스",
  "defaultPinned": false,
  "order": 100
}
```

외부 URL은 `https://`만 허용합니다. 원격 favicon, OpenGraph, iframe, health check, prefetch를 사용하지
않습니다. 링크는 `target="_blank" rel="noopener noreferrer"`로 렌더링됩니다.

고정 앱 ID는 `my-workspace:pinned-apps:v1` localStorage key에 저장합니다. 손상되거나 삭제된 앱 ID는
무시하고 registry 기본값으로 복구합니다.
