# Shared Markdown 사용자 데이터 기준선 갱신

기록 시각: 2026-07-29T16:34:25+09:00

사용자가 구현 중 직접 추가한 데이터임을 확인했으므로 아래 상태를 새 불변 기준선으로 사용한다.
테스트 fixture나 migration으로 생성된 데이터가 아니며 기존 데이터는 복원하거나 삭제하지 않았다.

| DB | SHA-256 | rows | schema SHA-256 |
|---|---|---:|---|
| Calendar | `11fb8dd76b6f129666e374a4a5ec78e66bd890373a9a761cdebdfb4d6739d08f` | 5 | `6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567` |
| Notes | `add24ef1c94a453c35469aee08bf138ef5549157f423dc7544a914d80d9411f8` | 1 | `77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32` |

- Calendar quick_check: ok; user_version: 2
- Notes quick_check: ok; user_version: 1
- 이후 테스트는 임시 DB/E2E 전용 DB만 사용한다.
