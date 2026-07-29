# Adding an internal app

1. `apps/{id}/` 아래에 FastAPI app factory와 app 전용 template/static을 둡니다.
2. 운영/pytest/Chromium DB 경로를 factory 인자로 주입하고 다른 앱 DB와 분리합니다.
3. page template은 `shared/templates/app_shell.html`을 상속합니다.
4. API/static URL은 mount prefix에서 동작하도록 상대경로 또는 `root_path`를 사용합니다.
5. `shared/apps.json`에 검증 가능한 manifest를 추가합니다.
6. `portal/main.py`에서 sub-application을 명시적으로 import/mount합니다.
7. Portal과 앱 factory에 같은 `AppRegistry` 객체를 전달합니다.
8. 다른 앱 객체를 참조한다면 `SourceReferenceProvider`가 필요한지 검토합니다. 내부 HTTP,
   cross-DB FK/ATTACH/join/transaction은 만들지 않습니다.
9. repository/service/API/JS 테스트와 Registry surface 테스트를 추가합니다.
10. Chromium 전용 DB로 E2E를 실행하고 운영 DB checksum/rows/schema 불변을 확인합니다.

Registry 등록과 ASGI runtime mount는 서로 독립된 책임입니다. Registry import side effect나
filesystem 자동 탐색으로 앱을 실행하지 않습니다.
