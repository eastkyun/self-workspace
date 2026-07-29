# Shared Markdown Core

Notes와 Calendar는 `shared/static/markdown/`의 동일한 renderer, sanitizer,
command registry, editor controller와 viewer를 사용한다. 앱별 코드는 저장과 화면 배치만
담당하며 Markdown dialect나 보안 정책을 복제하지 않는다.

## 저장 및 렌더링 계약

- DB에는 Markdown 원문만 저장한다.
- Notes는 기존 `notes.content`, Calendar는 기존 `tasks.description` 컬럼을 그대로 사용한다.
- rendered/sanitized HTML, heading metadata와 TOC는 저장하지 않는다.
- 기존 API URL, request/response 필드와 status code를 변경하지 않는다.
- 기존 plain text는 `breaks: true`인 Markdown으로 무변환 해석한다.
- 목록 excerpt는 `extractExcerpt()`로 Markdown 문법을 제거한 plain text를 사용한다.

렌더링 순서:

```text
Markdown source
  → isolated Marked instance
  → raw HTML token renderer의 literal escape
  → generated HTML
  → DOMPurify strict allowlist DocumentFragment
  → preview/viewer replaceChildren()
```

Raw HTML은 제거하지 않고 문자 그대로 표시한다. 예를 들어
`<script>alert(1)</script>`는 화면에 해당 문자열이 보이지만 `script` DOM은 생성되지 않는다.
전체 Marked 결과를 일괄 escape하는 방식은 사용하지 않는다.

DOMPurify는 script, iframe, form, input, button, style, SVG, image와 event handler,
`javascript:`, `vbscript:`, 위험한 `data:` URL을 차단한다. 외부 링크는
`rel="noopener noreferrer"`를 사용하며 새 창 열기는 명시적인 render option일 때만 허용한다.

현재 이미지는 지원하지 않는다. clipboard image 기능은 Base64 본문 저장이 아니라 별도의
로컬 attachment 저장소와 same-origin URL allowlist를 갖춘 후속 기능으로 구현해야 한다.

## Public browser API

`shared/static/markdown/index.js`가 public entry point다.

- `createMarkdownRenderer()`:
  - `render()`, `renderInline()`, `renderWithMetadata()`
  - `collectHeadings()`, `extractPlainText()`, `extractExcerpt()`
  - `renderWithMetadata()`는 직렬화된 `html`과 sanitized `fragment`,
    heading/TOC/internal-link metadata 및 warning을 반환한다.
- `attachMarkdownEditor(textarea, options)`
- `createMarkdownToolbar(options)`
- `attachSlashPalette(textarea, controller, options)`
- `attachMarkdownPreview(textarea, container, options)`
- `mountMarkdownViewer(container, markdown, options)`
- `COMMANDS`, `applyCommand()`, `searchCommands()`
- `collectInternalLinks()`, `resolveInternalLinks()`

Editor instance는 전역 편집 상태를 공유하지 않는다. `destroy()`는 event listener와 생성한 DOM을
정리한다. Calendar modal은 editor를 한 번 mount하고 열기/닫기 시 source와 mode만 갱신한다.

## Dialect

지원:

- H1–H6 heading과 deterministic ID
- paragraph와 plain-text line break
- bold, italic, strikethrough
- unordered, ordered, nested, task list
- blockquote
- inline/fenced code
- horizontal rule
- link와 GFM table
- escaped Markdown character
- typed internal link

실행 가능한 raw HTML, image, iframe, SVG, arbitrary component, Mermaid, math, footnote와
syntax-highlighting plugin은 지원하지 않는다.

중복 heading은 `설치-방법`, `설치-방법-2` 순서로 suffix를 갖는다. TOC는 renderer가 반환한
동일 heading metadata를 사용하며 DOM을 다시 분석하지 않는다. Heading permalink는 동일 ID를
사용하고, TOC는 범위 내 heading이 두 개 이상일 때만 표시한다.

## Editor profile

- `full`: Notes. 전체 toolbar, slash palette, editor/split/preview, TOC.
- `compact`: Calendar. 핵심 toolbar, slash palette, editor/preview, compact viewer.
- `minimal`: 향후 작은 입력 영역용 핵심 명령.

Profile은 command 노출과 UI mode만 변경한다. Markdown dialect와 sanitizer 정책은 어떤
profile에서도 동일하다. Toolbar와 slash palette는 모두 `COMMANDS` registry를 조회한다.

Slash palette는 줄 시작 또는 indentation 뒤 `/`에서 열리며 문장 중간 공백 뒤, URL, 단어
중간, inline/fenced code와 IME composition 중에는 열리지 않는다. Arrow keys와 Enter로
명령을 선택하고 Escape로 닫는다. Tab은 palette만 닫고 기본 focus traversal을 보존한다.
한글/영문 alias를 모두 지원하며 palette는 textarea caret 주위에서 viewport 안으로 보정된다.

200,000자를 초과하는 입력은 parser 예외로 UI를 중단하지 않는다. literal plain-text
fallback과 `source-too-large` warning을 반환하며 실행 가능한 DOM을 만들지 않는다.

## Internal link extension

MVP 문법:

```md
[[note:13]]
[[note:13|회의 기록]]
[[calendar:14]]
[[calendar:14|일정]]
```

`shared/internal_links.py`는 immutable reference/result와 provider protocol, resolver registry를
제공한다. `portal.main`이 Notes와 Calendar provider를 명시적으로 조합하며 filesystem discovery나
import side effect는 없다.

```http
POST /api/internal-links/resolve
Content-Type: application/json

{"links":[{"type":"note","target":"13"}]}
```

Provider는 자신의 DB를 read-only로 조회한다. cross-database join/FK/transaction은 없다.
반환 href는 App Registry가 인식하는 `/apps/...` same-workspace route인지 검증된다. 지원하지
않거나 존재하지 않거나 실패한 링크는 anchor가 아닌 `.internal-link.broken`으로 표시한다.

새 type 추가 절차:

1. 앱 공식 read-only service/repository를 사용하는 `InternalLinkProvider`를 작성한다.
2. target validation과 deterministic label/href를 테스트한다.
3. `portal.main` composition root에 provider를 명시적으로 등록한다.
4. arbitrary host, scheme, encoded traversal 반환이 거부되는지 테스트한다.
5. Markdown Core에 앱별 `if` 분기를 추가하지 않는다.

## 로컬 dependency와 검증

- Marked `18.0.7`
- DOMPurify `3.4.12`

브라우저 runtime은 npm registry나 CDN에 접근하지 않는다. 버전이 고정된 ESM 파일과 license는
`shared/static/markdown/vendor/`에 있다.

```bash
npm run verify:markdown-vendor
npm run test:markdown
node --test tests/notes/*.mjs tests/calendar/*.mjs
python -m pytest -q tests --ignore=tests/e2e
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
  python -m pytest -q tests/e2e
```

회귀 검증은 320, 375, 768, 1024, 1440 viewport, horizontal overflow, same-origin request,
failed request, console error, raw HTML literal 표시, keyboard/IME와 modal reopen을 포함한다.
