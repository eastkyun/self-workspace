# Calendar SQLite 백업·검증·복원

운영 DB 기본 경로는 `instance/calendar/tasks.db`입니다. Git에서 제외됩니다.

## 안전 원칙

1. 원본 `/home/eastvirus/projects/todo-app/instance/**`는 수정하지 않습니다.
2. 실행 중 단순 파일 복사 대신 Python `sqlite3.Connection.backup()`을 사용합니다.
3. migration은 destination이 아닌 staging DB에서만 수행합니다.
4. destination이 이미 존재하면 덮어쓰지 않습니다.
5. Git rollback과 DB rollback은 별도로 수행합니다.

## 검증 항목

- SHA-256와 파일 크기
- `PRAGMA quick_check`
- `PRAGMA integrity_check`
- `PRAGMA foreign_key_check`
- `PRAGMA user_version`
- `tasks`, `topics`, `task_topics` 행 수
- ID 범위, 일정 시각, 주제 연결

## promote

검증된 staging 파일은 destination과 동일한 `instance/calendar/` 파일시스템에 둡니다.
서버가 중지되고 destination이 없음을 다시 확인한 뒤 `os.replace()`로
`.tasks.<timestamp>.staging.db`를 `tasks.db`에 원자적으로 promote합니다.

실제 promote 전에는 반드시 staging 체크섬과 검증 결과를 사용자에게 보여주고 별도 승인을 받습니다.

## rollback

1. Portal 서버를 중지합니다.
2. 실패한 destination을 타임스탬프가 붙은 격리 이름으로 옮겨 보존합니다.
3. 검증된 pre-promote backup을 새 staging 파일로 복구합니다.
4. 동일 검증을 반복합니다.
5. 검증 후 원자적으로 destination에 promote합니다.

원본 저장소와 원본 DB는 사용자의 별도 삭제 승인 없이는 보존합니다.
