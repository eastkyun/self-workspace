# Notes architecture

## Runtime

`portal.main.create_app()`이 Calendar DB와 Notes DB 경로를 각각 해석하고 두 sub-application을
명시적으로 mount합니다. Notes app factory는 DB 경로, shared `AppRegistry`, 그리고
`SourceReferenceResolver`를 주입받습니다. Registry metadata는 runtime mount를 실행하지 않습니다.

## 책임

- `database.py`: Notes DB schema v1, lifecycle, quick/FK/version 검증
- `repository.py`: parameterized SQLite CRUD, 검색, 정렬, pagination, link SQL
- `service.py`: timestamp, not-found/duplicate/source 규칙, transaction 조정
- `schemas.py`: HTTP 입력/출력 validation
- `main.py`: FastAPI route, status/error 변환, App Shell route
- `static/`: framework 없는 list/editor state와 mounted-relative API adapter

## Schema

`notes`는 `id`, `title`, `content`, `note_type`, `is_pinned`, 생성/수정 UTC timestamp를
저장합니다. 기본 정렬은 `is_pinned DESC, updated_at DESC, id DESC`입니다.

`note_links`는 Notes DB 안에서만 `notes.id`를 FK로 참조합니다. 외부 객체는
`source_app`, `source_type`, `source_id`, `source_title_snapshot`으로 표현하며 물리적 FK가
없습니다. DB schema version은 `PRAGMA user_version=1`입니다.

## Search

검색어 앞뒤 공백을 제거하고 `%`, `_`, `\\`를 literal로 escape한 SQLite LIKE를 제목과 본문에
적용합니다. 빈 검색어는 전체 목록입니다. SQLite 기본 LIKE의 Unicode case-folding 한계는
MVP 제약이며 FTS5는 사용하지 않습니다.
