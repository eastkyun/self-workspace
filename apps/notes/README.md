# Notes

Workspace의 독립 문서 앱입니다. Shared App Shell과 동일한 `AppRegistry` snapshot을 사용하며
`/apps/notes`에 FastAPI sub-application으로 명시적으로 mount됩니다.

## 기능

- 일반 메모, 회의록, 연구 노트 CRUD
- 제목/본문 LIKE 검색, 유형 필터, 최근 수정/고정 정렬
- 앱 ID 기반 Workspace pin과 독립적인 Note pin
- Calendar event를 가리키는 논리적 link
- Calendar event 삭제 후 Note, link, title snapshot 보존과 `orphan=true`

## 데이터

기본 DB는 `instance/notes/notes.db`입니다. `NOTES_DB_PATH`로 주입할 수 있습니다.
Calendar DB를 `ATTACH`하지 않고 cross-database join/transaction/FK를 만들지 않습니다.
`note_links.note_id`만 Notes DB 내부 FK로 관리됩니다.

## URL

- UI: `/apps/notes/`, `/apps/notes/new`, `/apps/notes/?note_id={id}`
- CRUD: `/apps/notes/api/notes`
- links: `/apps/notes/api/notes/{id}/links`

## 테스트

```bash
python -m pytest -q tests/notes
node --test tests/notes/*.mjs
```
