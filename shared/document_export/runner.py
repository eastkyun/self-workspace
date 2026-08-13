from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .contracts import ExportFormat
from .docx_style import style_docx
from .errors import ExportConversionFailed, ExportTimeout, ExportUnavailable

TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"
WORKSPACE_DIRECTORY = Path(__file__).resolve().parents[2]
UNICODE_FILTER = TEMPLATE_DIRECTORY / "unicode-fallback.lua"
UNICODE_HEADER = TEMPLATE_DIRECTORY / "unicode-fallback.tex"
PRINT_TEMPLATE = TEMPLATE_DIRECTORY / "print.html"
PRINT_STYLES = TEMPLATE_DIRECTORY / "print.css"
MARKDOWN_STYLES = WORKSPACE_DIRECTORY / "shared/static/markdown/markdown.css"
BROWSER_PDF_SCRIPT = Path(__file__).resolve().parent / "chromium_pdf.py"


@dataclass(frozen=True, slots=True)
class PandocRunner:
    pandoc_path: str = "pandoc"
    xelatex_path: str = "xelatex"
    chromium_path: str = "chromium"
    pdf_engine: str = "chromium"
    timeout_seconds: int = 30
    cjk_font: str = "Noto Sans CJK KR"
    reference_docx: Path | None = None

    def _executable(self, configured: str) -> str:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return str(configured_path.resolve())
        resolved = shutil.which(configured)
        if resolved is None:
            raise ExportUnavailable(f"required document converter is unavailable: {configured}")
        return resolved

    def _chromium_executable(self) -> str:
        try:
            return self._executable(self.chromium_path)
        except ExportUnavailable:
            candidates = (
                *WORKSPACE_DIRECTORY.glob(
                    ".playwright-browsers/chromium-*/chrome-linux*/chrome"
                ),
                *Path.home().glob(
                    ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"
                ),
            )
            for candidate in candidates:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
            raise ExportUnavailable("required document converter is unavailable: chromium")

    def _run(self, command: list[str], *, resource_path: Path) -> None:
        try:
            result = subprocess.run(
                command,
                cwd=resource_path,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(resource_path),
                    "LANG": "C.UTF-8",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ExportTimeout("document conversion timed out") from error
        except OSError as error:
            raise ExportUnavailable("document converter could not be started") from error
        if result.returncode != 0:
            raise ExportConversionFailed("document conversion failed")

    def convert(
        self,
        source: Path,
        output: Path,
        *,
        format: ExportFormat,
        resource_path: Path,
    ) -> None:
        pandoc = self._executable(self.pandoc_path)
        command = [
            pandoc,
            str(source),
            "--standalone",
            "--from=gfm+hard_line_breaks-raw_html",
            f"--resource-path={resource_path}",
            f"--output={output}",
            f"--lua-filter={UNICODE_FILTER}",
        ]
        if format == "docx" and self.reference_docx is not None:
            if not self.reference_docx.is_file():
                raise ExportUnavailable("configured DOCX reference document is unavailable")
            command.append(f"--reference-doc={self.reference_docx}")
        if format == "pdf" and self.pdf_engine == "xelatex":
            xelatex = self._executable(self.xelatex_path)
            command.extend(
                [
                    f"--pdf-engine={xelatex}",
                    "-V",
                    f"CJKmainfont={self.cjk_font}",
                    "-V",
                    "geometry:margin=22mm",
                    "-V",
                    "colorlinks=true",
                    f"--include-in-header={UNICODE_HEADER}",
                ]
            )
            self._run(command, resource_path=resource_path)
        elif format == "pdf" and self.pdf_engine == "chromium":
            html_output = resource_path / "document.html"
            command[command.index(f"--output={output}")] = f"--output={html_output}"
            command.extend(
                [
                    "--to=html5",
                    "--embed-resources",
                    f"--template={PRINT_TEMPLATE}",
                    f"--css={MARKDOWN_STYLES}",
                    f"--css={PRINT_STYLES}",
                ]
            )
            self._run(command, resource_path=resource_path)
            chromium = self._chromium_executable()
            chromium_command = [
                sys.executable,
                str(BROWSER_PDF_SCRIPT),
                chromium,
                str(html_output),
                str(output),
                str(self.timeout_seconds * 1000),
            ]
            self._run(chromium_command, resource_path=resource_path)
        elif format == "pdf":
            raise ExportUnavailable("configured PDF engine is unsupported")
        else:
            self._run(command, resource_path=resource_path)
            style_docx(output)
        if not output.is_file():
            raise ExportConversionFailed("document conversion failed")
