# DOCX/PDF document export

Notes의 저장된 현재 문서와 공개 중인 immutable Publication version은 서버에서
DOCX 또는 PDF로 변환할 수 있습니다.

## Runtime dependencies

- Pandoc: Markdown에서 DOCX를 생성하고 PDF 파이프라인을 조정합니다.
- Headless Chromium: 기존 Markdown CSS를 적용한 기본 PDF를 생성합니다.
- XeLaTeX: `DOCUMENT_EXPORT_PDF_ENGINE=xelatex` 선택 시 문서 중심 PDF를 생성합니다.
- `Noto Sans CJK KR` 또는 `DOCUMENT_EXPORT_CJK_FONT`에 지정한 CJK 글꼴.
- DejaVu Sans와 Unifont Upper: 체크박스·화살표·기호·emoji의 monochrome fallback.

DOCX와 PDF는 모두 A4, 12 mm 여백, 흰 문서 배경과 `UTC+09:00` 날짜를 사용합니다.
DOCX는 Pandoc 생성 후 OOXML 스타일을 정규화하여 본문에는 `Noto Sans CJK KR`, 코드에는
`Noto Sans Mono CJK KR`, 기호 run에는 `DejaVu Sans`를 지정합니다. 문단과 표 행에는
페이지 내 유지 강제를 적용하지 않아 페이지 경계에서 다음 장으로 자연스럽게 이어집니다.

설치 후 다음 명령이 성공해야 합니다.

```bash
pandoc --version
xelatex --version
chromium --version
fc-match "Noto Sans CJK KR"
```

변환기는 사용자 입력을 shell command로 실행하지 않습니다. Pandoc은 고정 argument
목록, 요청별 임시 작업 디렉터리와 local resource path로 실행됩니다. 기본 PDF는
기존 `shared/static/markdown/markdown.css`와 print stylesheet를 HTML에 embed한 뒤
Playwright가 관리하는 headless Chromium의 `page.pdf()`로 출력합니다. 원격/임의 파일
이미지 문법은 변환 전에 제거되고 권한 검사를 통과해 staging된 asset만 전달됩니다.
운영 프로세스도 비권한 사용자와 읽기 전용 application filesystem으로 실행해야 합니다.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCUMENT_EXPORT_PANDOC_PATH` | `pandoc` | Pandoc executable |
| `DOCUMENT_EXPORT_XELATEX_PATH` | `xelatex` | XeLaTeX executable |
| `DOCUMENT_EXPORT_CHROMIUM_PATH` | `chromium` | Headless Chromium executable |
| `DOCUMENT_EXPORT_PDF_ENGINE` | `chromium` | Styled HTML 또는 XeLaTeX PDF 선택 |
| `DOCUMENT_EXPORT_CJK_FONT` | `Noto Sans CJK KR` | Embedded PDF font |
| `DOCUMENT_EXPORT_TIMEOUT_SECONDS` | `60` | Per-request timeout |
| `DOCUMENT_EXPORT_MAX_CONCURRENT` | `2` | In-process concurrency cap |
| `DOCUMENT_EXPORT_REFERENCE_DOCX` | unset | Optional reviewed reference DOCX |

DOCX reference file의 생성 절차는
`shared/document_export/templates/README.md`에 있습니다.

## HTTP behavior

- Notes: `GET /api/v1/workspaces/{workspace_id}/notes/{note_id}/export?format=docx|pdf`
- Publication: `GET /api/publications/public/{public_id}/export?format=docx|pdf`

Notes endpoint는 기존 workspace 인증과 `notes.read_all` 권한을 적용합니다. Publication
endpoint는 현재 공개 version만 조회하며 archive된 발행물이나 공개 projection에 없는
이미지는 제공하지 않습니다.

| Status | Meaning |
| --- | --- |
| `404` | Note/Publication이 없거나 현재 공개되지 않음 |
| `422` | format 또는 source/resource 제한 위반 |
| `503` | 변환기/reference file 없음 또는 동시 처리 용량 소진 |
| `504` | 변환 timeout |
| `500` | 변환기가 실패했으며 상세 stderr는 사용자에게 숨김 |

변환 실패 시 앱의 읽기, 편집, 발행 기능은 계속 동작합니다. 임시 source, staged image,
결과 파일은 요청 종료 시 삭제되며 영구 export cache는 만들지 않습니다.

## Verification

도구 없이 실행 가능한 계약 테스트:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  -p no:cacheprovider tests/shared/test_document_export.py
```

운영 이미지에서는 한국어, 표, fenced code, task list, PNG/JPEG/WebP가 포함된 fixture를
두 형식으로 내려받아 Word/LibreOffice 및 PDF reader에서 열리는지 smoke test합니다.
