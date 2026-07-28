# Calendar Diary

FastAPI와 SQLite로 만든 단일 사용자용 월간 일정 관리 앱입니다. 로컬에 포함한
FullCalendar로 여러 날짜에 걸친 일정을 표시하고, `할 일`, `진행 중`, `완료`
상태, 여러 주제, 마감 초과 여부를 함께 관리합니다.

## 주요 기능

- 월간 캘린더와 선택한 날짜의 일정 목록
- 시간 일정 및 종일 일정 추가, 수정, 삭제
- 상태별 필터와 마감 초과 미완료 필터
- 이름과 색상을 가진 주제 관리 및 일정별 다중 주제 지정
- 선택 날짜의 전체, 미분류, 주제별 색상 칩 필터
- 데스크톱 우측 일정 패널과 모바일 하단 일정 패널
- SQLite 영구 저장 및 기존 칸반 데이터베이스 자동 마이그레이션
- 외부 CDN 없이 동작하는 FullCalendar 로컬 정적 자산
- Python API/마이그레이션 테스트와 Node 캘린더 계산 테스트

인증, 다중 사용자/캘린더, 반복 일정, 알림, 시간대 선택, 캘린더
드래그/리사이즈는 현재 범위에 포함되지 않습니다.

## 요구 사항

- Python 3.11 이상
- JavaScript 단위 테스트를 실행하려면 Node.js 18 이상

## 설치 및 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

브라우저에서 <http://127.0.0.1:8000>을 엽니다. 기본 데이터베이스는
`instance/tasks.db`입니다. 다른 경로를 사용하려면 다음과 같이 지정합니다.

```bash
TODO_DB_PATH=/path/to/tasks.db uvicorn app.main:app
```

## KST 일정 시각 계약

앱의 업무 시간대는 서버와 브라우저의 시간대와 관계없이
`Asia/Seoul`(KST, UTC+09:00)로 고정됩니다.

- API와 DB의 `start_at`, `due_at`은
  `YYYY-MM-DDTHH:MM:SS+09:00` 형식으로 정규화됩니다.
- API에 UTC나 다른 offset의 시각을 보내면 동일한 순간의 KST 시각으로
  변환합니다. offset이 없는 시각은 `422`로 거부합니다.
- 유효한 기간은 `start_at <= due_at`입니다. 두 값이 같은 마감 시점형
  일정도 허용합니다.
- 마감 초과는 `status != "done"`이고 `due_at`이 현재 KST 순간보다
  **엄격히 이전**인 경우입니다. 현재 순간과 같으면 마감 초과가 아닙니다.
- 시간 일정은 실제 `due_at`을 FullCalendar의 `end`로 그대로 전달합니다.
  시작과 마감이 같을 때만 캘린더 표시를 위해 `end`를 생략하고 실제 값은
  별도로 보존합니다.
- 종일 일정은 DB/API에서 첫날 `00:00:00+09:00`부터 마지막 날
  `23:59:59+09:00`까지 양 끝 날짜를 포함합니다. FullCalendar의 `end`는
  exclusive이므로 화면에는 마지막 날의 다음 날짜를 전달합니다.

예를 들어 7월 28일 하루 종일인 일정은 API에서
`2026-07-28T00:00:00+09:00`부터 `2026-07-28T23:59:59+09:00`이고,
FullCalendar에는 `start: "2026-07-28"`, `end: "2026-07-29"`로
표시합니다.

## 기존 DB 마이그레이션

현재 데이터베이스 스키마는 v2입니다. 앱 시작 시 기존 v0 칸반 또는 v1 캘린더
DB를 자동으로 마이그레이션합니다.

### v0 칸반 → v2

1. 마이그레이션 전에 DB와 같은 디렉터리에
   `tasks.db.pre-calendar-v0.bak` 백업을 한 번 만듭니다.
   `TODO_DB_PATH`를 사용하면 백업 경로는 지정한 경로 뒤에
   `.pre-calendar-v0.bak`을 붙인 값입니다.
2. 기존 ID, 제목, 설명, 상태, 위치, 생성/수정 시각은 보존합니다.
3. 기존 작업의 timezone-aware `created_at`을 KST로 변환해 `start_at`으로
   사용하고, `due_at`은 한 시간 뒤로 임시 설정합니다. `all_day`는
   `false`입니다.
4. `created_at`을 사용할 수 없으면 timezone-aware `updated_at`을
   사용합니다. 둘 다 유효하지 않으면 임의 값을 쓰지 않고 마이그레이션을
   중단합니다.
5. 일정 변환 후 주제 테이블을 추가하며, 기존 일정은 연결된 주제가 없는
   `미분류` 일정이 됩니다.
6. 성공 후 SQLite `user_version`은 `2`이며, 이후 시작은 데이터를 다시
   변환하지 않습니다.

마이그레이션된 임시 일정 시각은 앱에서 실제 일정에 맞게 수정하십시오.
복구가 필요하면 앱을 중지하고 현재 DB를 별도 보관한 뒤
`tasks.db.pre-calendar-v0.bak`을 원래 DB 경로로 복사합니다.

### v1 캘린더 → v2

1. 마이그레이션 전에 `tasks.db.pre-topics-v1.bak` 백업을 한 번 만듭니다.
2. 기존 일정 행과 시각은 변경하지 않고 `topics`, `task_topics` 테이블을
   추가합니다.
3. 기존 일정은 주제 연결이 없으므로 UI의 `미분류` 칩에서 조회됩니다.
4. 주제를 삭제하면 일정은 유지되고 연결만 삭제됩니다.

각 주제는 대소문자를 구분하지 않는 고유 이름과 `#RRGGBB` 색상을 저장합니다.
서로 다른 주제가 같은 색상을 사용하는 것은 허용됩니다.

## API

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/tasks` | 전체 일정을 시작/마감 시각순으로 조회 |
| `POST` | `/api/tasks` | 일정 생성 |
| `PATCH` | `/api/tasks/{id}` | 일정 필드 또는 호환용 위치 변경 |
| `DELETE` | `/api/tasks/{id}` | 일정 삭제 |
| `GET` | `/api/topics` | 주제 이름순 조회 |
| `POST` | `/api/topics` | 주제 생성 |
| `PATCH` | `/api/topics/{id}` | 주제 이름 또는 색상 수정 |
| `DELETE` | `/api/topics/{id}` | 주제 연결 삭제, 일정 유지 |

시간 일정 생성 예시:

```json
{
  "title": "README 검토",
  "description": "실행 방법 확인",
  "status": "todo",
  "all_day": false,
  "start_at": "2026-07-28T09:30:00+09:00",
  "due_at": "2026-07-28T10:30:00+09:00",
  "topic_ids": [1, 2]
}
```

종일 일정 생성 예시:

```json
{
  "title": "분기 계획",
  "description": "",
  "status": "doing",
  "all_day": true,
  "start_at": "2026-07-28T00:00:00+09:00",
  "due_at": "2026-07-30T23:59:59+09:00"
}
```

`all_day: true`이면 서버는 입력 시각의 KST 날짜를 사용해 시작을
`00:00:00`, 마감을 `23:59:59`로 정규화합니다.

부분 수정 예시:

```json
{
  "status": "done",
  "due_at": "2026-07-28T11:00:00+09:00"
}
```

`PATCH`에서 시작 또는 마감 하나만 보낼 수 있지만, 서버는 저장된 반대편
값과 합친 최종 기간이 유효한지 검사합니다. 기존 호환성을 위해
`position` 필드는 유지되지만 캘린더 UI에서 일정 순서를 드래그해 변경하지
않습니다.

주제 생성 예시:

```json
{
  "name": "업무",
  "color": "#3366CC"
}
```

일정 생성과 수정의 `topic_ids`는 여러 ID를 받을 수 있습니다. PATCH에서
`topic_ids`를 생략하면 기존 연결을 유지하고, 빈 배열을 보내면 모든 연결을
제거합니다. 일정 응답에는 이름순 `topics` 배열이 포함됩니다.

## FullCalendar 로컬 자산

이 저장소는 **FullCalendar Standard 7.0.2**의 공식 global bundle과 Classic
테마를 `app/static/vendor/fullcalendar/7.0.2/`에 고정합니다. Standard
비-Premium 구성요소는 MIT 라이선스이며, upstream `LICENSE.md`와 다운로드
출처, ZIP SHA-256, 포함 파일 목록은 같은 디렉터리의 `VENDOR.md`에
보존합니다.

브라우저는 `/static/vendor/...` 파일만 사용하며 CDN이나 런타임 외부
네트워크 요청을 사용하지 않습니다. 버전을 갱신할 때는 공식 release ZIP을
받아 `VENDOR.md`의 URL과 SHA-256을 검증하고, 필요한 Standard 파일만 새
버전 디렉터리에 복사한 뒤 HTML 경로와 전체 테스트를 함께 갱신하십시오.
vendor 파일 자체는 수정하지 않습니다.

## 테스트

개발 의존성을 설치한 뒤 Python과 JavaScript 테스트를 각각 실행합니다.

```bash
python3 -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --test tests/test_calendar.mjs
```

JavaScript 테스트는 npm 패키지 없이 Node 내장 `node:test`를 사용합니다.
KST 변환, 시간/종일 exclusive end, 같은 날·자정·여러 날·월말 경계,
선택 날짜 포함, 상태/주제/미분류 필터, 마감 초과 equality 경계 및 여러 호스트
시간대(`UTC`, `America/New_York`, `Asia/Seoul`)의 동일 결과를 검증합니다.
Python 테스트는 임시 SQLite DB를 사용하므로 실제 데이터에 영향을 주지
않습니다.

## 프로젝트 구조

```text
app/
  main.py                              FastAPI, SQLite, v0/v1→v2 마이그레이션, REST API
  static/
    index.html                         월간 캘린더 및 일정 폼
    styles.css                         다이어리 테마와 반응형 레이아웃
    app.js                             FullCalendar 초기화와 CRUD 연결
    calendar-adapter.js                KST, 주제 필터 및 API↔캘린더 순수 변환
    vendor/fullcalendar/7.0.2/         로컬 FullCalendar와 라이선스
tests/
  test_api.py                          일정/주제 API와 정적 자산 통합 테스트
  test_migration.py                    v0/v1 DB 마이그레이션 테스트
  test_calendar.mjs                    캘린더·주제 어댑터 Node 단위 테스트
```
