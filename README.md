# Simple Kanban

FastAPI와 SQLite로 만든 단일 사용자용 칸반 할 일 관리 웹서비스입니다. 작업을 `할 일`, `진행 중`, `완료` 세 상태로 관리하고 카드 드래그앤드롭으로 이동하거나 재정렬할 수 있습니다.

## 주요 기능

- 세로 컬럼 3개로 구성된 데스크톱 우선 칸반 보드
- 작업 추가, 수정, 삭제
- 컬럼 간 이동과 컬럼 내부 순서 변경
- SQLite 영구 저장
- 모바일 가로 스크롤과 폼 기반 상태 변경
- API 및 데이터 정렬 자동 테스트

## 요구 사항

- Python 3.11 이상

## 설치 및 실행

이 환경에서는 `python` 대신 `python3`를 사용할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

브라우저에서 <http://127.0.0.1:8000>을 엽니다. 기본 데이터베이스는 `instance/tasks.db`에 생성됩니다.

다른 DB 경로를 사용하려면 환경 변수를 설정합니다.

```bash
TODO_DB_PATH=/path/to/tasks.db uvicorn app.main:app
```

## 테스트

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

각 테스트는 별도의 임시 SQLite 데이터베이스를 사용하므로 실제 데이터에 영향을 주지 않습니다.

## API

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/tasks` | 전체 작업 조회 |
| `POST` | `/api/tasks` | 작업 생성 |
| `PATCH` | `/api/tasks/{id}` | 작업 또는 위치 변경 |
| `DELETE` | `/api/tasks/{id}` | 작업 삭제 |

생성 예시:

```json
{
  "title": "README 검토",
  "description": "실행 방법 확인",
  "status": "todo"
}
```

위치 변경 예시:

```json
{
  "status": "doing",
  "position": 0
}
```

## 프로젝트 구조

```text
app/
  main.py          FastAPI 앱, SQLite 데이터 접근, REST API
  static/
    index.html     칸반 화면
    styles.css     레이아웃과 반응형 스타일
    app.js         CRUD, 렌더링, 드래그앤드롭
tests/
  test_api.py      API, 정렬, 정적 파일 테스트
```

## 현재 범위

초기 버전은 단일 사용자와 단일 보드에 집중합니다. 인증, 다중 보드, 라벨, 마감일, 검색, 실시간 협업은 포함하지 않습니다. 데스크톱에서는 드래그앤드롭을 지원하며, 모바일에서는 작업 편집 폼의 상태 선택으로 확실하게 이동할 수 있습니다.
