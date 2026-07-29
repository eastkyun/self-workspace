# Cross-app linking

## SourceReference

```json
{
  "source_app": "calendar",
  "source_type": "event",
  "source_id": "123"
}
```

`shared.source_references`가 immutable reference/result, provider Protocol, explicit resolver
mapping을 제공합니다. 현재 provider는 `calendar/event` 하나이며 Calendar DB를 read-only
SQLite URI로 조회합니다. localhost HTTP, filesystem discovery, plugin runtime은 사용하지
않습니다.

## 생성과 오류

Notes service가 provider로 source 존재를 먼저 확인하고 provider가 반환한 현재 title을
snapshot으로 저장합니다.

- malformed source 또는 미지원 app/type: 422
- 존재하지 않는 Calendar event: 404
- 동일 Note/event 중복: 409
- 존재하지 않는 Note/link: 404

SQLite constraint 문자열은 API에 노출하지 않습니다.

## 삭제와 orphan

Calendar event 삭제는 Notes DB에 write하지 않습니다. Note, link row, title snapshot은
그대로 유지됩니다. Link 조회 때 provider가 source를 찾지 못하면 API가 `orphan=true`를
계산하며 UI는 snapshot과 “삭제된 일정” 상태를 표시합니다. 사용자가 명시적으로 연결 해제를
선택한 경우에만 link row를 삭제합니다.

## 확장

새 source 유형은 `(source_app, source_type)` key와 read-only provider를 Portal composition
root에 명시적으로 등록합니다. Notes schema나 API를 앱별 필드로 확장하지 않습니다.
