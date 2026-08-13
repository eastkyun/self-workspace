# Document export templates

`DocumentExportService` uses Pandoc's default DOCX reference document unless
`DOCUMENT_EXPORT_REFERENCE_DOCX` points to a reviewed custom reference file.
After Pandoc runs, the exporter normalizes either reference to the shared print
system: white A4 pages, 12 mm margins, compact heading/body sizes, Korean
language metadata, Noto CJK fonts, DejaVu symbol runs, and freely splittable
paragraphs/table rows. Export timestamps are emitted as UTC+09:00.

Generate a starting reference file with the installed, pinned Pandoc version:

```sh
pandoc --print-default-data-file reference.docx > reference.docx
```

Edit styles, headers, and footers only, then configure its absolute path. The
export postprocessor deliberately owns fonts, page size, margins, background,
and pagination so custom references cannot accidentally reintroduce ink-heavy
page colors or incompatible typography. PDF output defaults to the Chromium
HTML print pipeline; XeLaTeX remains available as a fallback.
