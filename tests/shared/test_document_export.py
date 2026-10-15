from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from shared.document_export import (
    AssetResource,
    DocumentExportService,
    DocumentSnapshot,
    ExportResourceLimitExceeded,
    ExportUnavailable,
    PandocRunner,
    attachment_headers,
)
from shared.document_export.markdown import MAX_ASSET_BYTES, normalize_markdown
from shared.document_export.service import export_date, safe_filename
from shared.document_export.chromium_pdf import (
    HEADING_PAGE_BREAK_THRESHOLD,
    PRINTABLE_A4_HEIGHT_MM,
    apply_heading_page_breaks,
)
from shared.document_export.runner import PRINT_STYLES
from shared.document_export.docx_style import style_docx


class FakeRunner:
    def __init__(self) -> None:
        self.source = ""
        self.resource_files: tuple[str, ...] = ()
        self.resource_root: Path | None = None

    def convert(self, source, output, *, format, resource_path) -> None:
        self.source = Path(source).read_text(encoding="utf-8")
        self.resource_root = Path(resource_path)
        self.resource_files = tuple(
            sorted(
                str(path.relative_to(resource_path))
                for path in Path(resource_path).rglob("*")
                if path.is_file()
            )
        )
        output.write_bytes(b"PK-docx" if format == "docx" else b"%PDF-fake")


def test_export_builds_metadata_and_unicode_attachment() -> None:
    runner = FakeRunner()
    service = DocumentExportService(runner=runner)

    exported = service.export(
        DocumentSnapshot(
            title='회의록 "주간"',
            markdown="첫 줄\n둘째 줄",
            author="홍길동",
            date="2026-08-13",
        ),
        "docx",
    )

    assert exported.content == b"PK-docx"
    assert exported.filename == '회의록 -주간-.docx'
    assert 'title: "회의록 \\"주간\\""' in runner.source
    assert runner.resource_root is not None
    assert not runner.resource_root.exists()
    headers = attachment_headers(exported.filename)
    assert "filename*=UTF-8''" in headers["Content-Disposition"]
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-13T01:02:03Z", "2026-08-13T10:02:03+09:00"),
        ("2026-08-13T01:02:03+00:00", "2026-08-13T10:02:03+09:00"),
        ("2026-08-13T10:02:03+09:00", "2026-08-13T10:02:03+09:00"),
        ("2026-08-13", "2026-08-13"),
    ],
)
def test_export_date_uses_korea_standard_time(value: str, expected: str) -> None:
    assert export_date(value) == expected


def test_print_styles_use_plain_white_pages_and_allow_content_fragmentation() -> None:
    styles = PRINT_STYLES.read_text(encoding="utf-8")
    assert "background: #fff;" in styles
    assert "border: 0;" in styles
    assert "box-shadow: none;" in styles
    assert "break-inside: avoid" not in styles
    assert "break-after: avoid" not in styles


def test_print_styles_support_heading_page_breaks() -> None:
    styles = PRINT_STYLES.read_text(encoding="utf-8")
    assert ".markdown-viewer__content .pdf-page-break-before" in styles
    assert "break-before: page;" in styles


def test_chromium_marks_body_headings_after_the_page_threshold() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.script = ""
            self.argument = {}

        def evaluate(self, script, argument):
            self.script = script
            self.argument = argument
            return 2

    page = FakePage()

    assert apply_heading_page_breaks(page) == 2
    assert page.argument == {
        "printableHeightMm": PRINTABLE_A4_HEIGHT_MM,
        "threshold": HEADING_PAGE_BREAK_THRESHOLD,
    }
    assert page.argument["threshold"] == 0.6
    assert ".markdown-viewer__content h1" in page.script
    assert ".markdown-viewer__content h2" in page.script
    assert ".markdown-viewer__content h3" not in page.script
    assert 'heading.classList.add("pdf-page-break-before")' in page.script


def test_docx_style_matches_white_a4_print_system_and_symbol_fallback(tmp_path: Path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:pPr><w:keepLines/></w:pPr><w:r><w:t>한글 ✓ 😀</w:t></w:r></w:p>
      <w:sectPr><w:pgSz/><w:pgMar/></w:sectPr></w:body>
    </w:document>"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
    <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:docDefaults/><w:style w:type="paragraph" w:styleId="Title"><w:pPr><w:keepNext/></w:pPr></w:style>
    </w:styles>"""
    output = tmp_path / "styled.docx"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)

    style_docx(output)

    with zipfile.ZipFile(output) as archive:
        styled_document = archive.read("word/document.xml").decode()
        styled_styles = archive.read("word/styles.xml").decode()
    assert 'w:color="FFFFFF"' in styled_document
    assert 'w:w="11906"' in styled_document
    assert 'w:left="680"' in styled_document
    assert "keepLines" not in styled_document
    assert "keepNext" not in styled_styles
    assert "Noto Sans CJK KR" in styled_styles
    assert 'w:val="172033"' in styled_styles
    assert "DejaVu Sans" in styled_document
    assert "Unifont Upper" in styled_document


def test_normalize_stages_allowed_images_and_degrades_missing_links(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image, format="PNG")

    def load(asset_id: int):
        if asset_id == 7:
            return AssetResource(image.getvalue(), "image/png")
        return None

    normalized = normalize_markdown(
        "![도표](image://7) ![비밀](image://8) ![외부](https://example.com/a.png) ![참조][remote] [[manual:guide|가이드]]\n\n[remote]: https://example.com/b.png",
        resource_directory=tmp_path,
        asset_loader=load,
    )

    assert "![도표](assets/7.png)" in normalized
    assert "비밀 [이미지를 사용할 수 없음]" in normalized
    assert "외부 [이미지를 사용할 수 없음]" in normalized
    assert "참조 [이미지를 사용할 수 없음]" in normalized
    assert "가이드" in normalized
    assert "[[" not in normalized
    assert (tmp_path / "assets/7.png").read_bytes() == image.getvalue()


def test_export_rejects_aggregate_image_limit(tmp_path: Path) -> None:
    resource = AssetResource(b"x" * (MAX_ASSET_BYTES + 1), "image/png")
    with pytest.raises(ExportResourceLimitExceeded, match="Images exceed"):
        normalize_markdown(
            "![large](image://1)",
            resource_directory=tmp_path,
            asset_loader=lambda _asset_id: resource,
        )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("../../secret", "-..-secret.pdf"),
        ("  ", "document.pdf"),
        ("보고서", "보고서.pdf"),
    ],
)
def test_safe_filename(title: str, expected: str) -> None:
    assert safe_filename(title, "pdf") == expected


def test_pandoc_runner_fails_cleanly_when_converter_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shared.document_export.runner.shutil.which", lambda _name: None)
    source = tmp_path / "document.md"
    source.write_text("safe", encoding="utf-8")

    with pytest.raises(ExportUnavailable, match="pandoc"):
        PandocRunner().convert(
            source,
            tmp_path / "document.docx",
            format="docx",
            resource_path=tmp_path,
        )


def test_pdf_runner_enables_unicode_fallback_assets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[str] = []

    def run(command, **_kwargs):
        captured.extend(command)
        output = next(
            item.removeprefix("--output=")
            for item in command
            if item.startswith("--output=")
        )
        Path(output).write_bytes(b"%PDF")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(
        "shared.document_export.runner.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr("shared.document_export.runner.subprocess.run", run)
    source = tmp_path / "document.md"
    source.write_text("✓ 😀", encoding="utf-8")

    PandocRunner(pdf_engine="xelatex").convert(
        source,
        tmp_path / "document.pdf",
        format="pdf",
        resource_path=tmp_path,
    )

    assert any(item.endswith("unicode-fallback.lua") for item in captured)
    assert any(item.endswith("unicode-fallback.tex") for item in captured)


def test_chromium_pdf_runner_uses_embedded_shared_styles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if any(item.startswith("--to=html5") for item in command):
            output = next(
                item.removeprefix("--output=")
                for item in command
                if item.startswith("--output=")
            )
            Path(output).write_text("<html>styled</html>", encoding="utf-8")
        else:
            Path(command[4]).write_bytes(b"%PDF-1.4\n%%EOF")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(
        "shared.document_export.runner.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr("shared.document_export.runner.subprocess.run", run)
    source = tmp_path / "document.md"
    source.write_text("# Styled", encoding="utf-8")

    PandocRunner(pdf_engine="chromium").convert(
        source,
        tmp_path / "document.pdf",
        format="pdf",
        resource_path=tmp_path,
    )

    assert "--embed-resources" in commands[0]
    assert any(item.endswith("print.html") for item in commands[0])
    assert any(item.endswith("markdown.css") for item in commands[0])
    assert any(item.endswith("print.css") for item in commands[0])
    assert commands[1][1].endswith("chromium_pdf.py")
