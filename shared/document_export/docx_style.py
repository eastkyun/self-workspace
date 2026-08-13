from __future__ import annotations

import copy
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
W = f"{{{WORD_NS}}}"

ET.register_namespace("w", WORD_NS)

BODY_FONT = "Noto Sans CJK KR"
MONO_FONT = "Noto Sans Mono CJK KR"
SYMBOL_FONT = "DejaVu Sans"
EMOJI_FONT = "Unifont Upper"

_STYLE_SIZES = {
    "Title": 38,
    "Subtitle": 28,
    "Heading1": 34,
    "Heading2": 28,
    "Heading3": 23,
    "Heading4": 20,
    "Heading5": 18,
    "Heading6": 18,
    "Author": 15,
    "Date": 15,
}


def _child(parent: ET.Element, name: str) -> ET.Element:
    element = parent.find(f"{W}{name}")
    if element is None:
        element = ET.SubElement(parent, f"{W}{name}")
    return element


def _set_font(run_properties: ET.Element, font: str) -> None:
    fonts = _child(run_properties, "rFonts")
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(f"{W}{attribute}", font)
    for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(f"{W}{attribute}", None)


def _allow_page_splitting(root: ET.Element) -> None:
    blocked = {f"{W}keepNext", f"{W}keepLines", f"{W}pageBreakBefore", f"{W}cantSplit"}
    for parent in root.iter():
        for child in list(parent):
            if child.tag in blocked:
                parent.remove(child)


def _style_styles(xml: bytes) -> bytes:
    root = ET.fromstring(xml)
    _allow_page_splitting(root)

    defaults = root.find(f"{W}docDefaults")
    if defaults is None:
        defaults = ET.Element(f"{W}docDefaults")
        root.insert(0, defaults)
    default_run = _child(_child(defaults, "rPrDefault"), "rPr")
    _set_font(default_run, BODY_FONT)
    _child(default_run, "sz").set(f"{W}val", "18")
    _child(default_run, "szCs").set(f"{W}val", "18")
    _child(default_run, "color").set(f"{W}val", "172033")
    language = _child(default_run, "lang")
    language.set(f"{W}val", "ko-KR")
    language.set(f"{W}eastAsia", "ko-KR")

    for style in root.findall(f"{W}style"):
        style_id = style.get(f"{W}styleId", "")
        run_properties = _child(style, "rPr")
        _set_font(run_properties, MONO_FONT if style_id == "VerbatimChar" else BODY_FONT)
        if style_id in _STYLE_SIZES:
            size = str(_STYLE_SIZES[style_id])
            _child(run_properties, "sz").set(f"{W}val", size)
            _child(run_properties, "szCs").set(f"{W}val", size)
        if style_id.startswith("Heading"):
            _child(run_properties, "color").set(f"{W}val", "1C4F43")
        if style_id == "Title":
            _child(run_properties, "color").set(f"{W}val", "172033")
            paragraph_properties = _child(style, "pPr")
            _child(paragraph_properties, "jc").set(f"{W}val", "left")
        elif style_id in {"Author", "Date"}:
            _child(run_properties, "color").set(f"{W}val", "667085")
        elif style_id == "Hyperlink":
            _child(run_properties, "color").set(f"{W}val", "2866A5")
        elif style_id == "BlockText":
            paragraph_properties = _child(style, "pPr")
            _child(paragraph_properties, "shd").set(f"{W}fill", "E9F4F0")
            borders = _child(paragraph_properties, "pBdr")
            left = _child(borders, "left")
            left.set(f"{W}val", "single")
            left.set(f"{W}sz", "18")
            left.set(f"{W}color", "357A68")
        elif style_id == "Table":
            first_row = next(
                (
                    item
                    for item in style.findall(f"{W}tblStylePr")
                    if item.get(f"{W}type") == "firstRow"
                ),
                None,
            )
            if first_row is None:
                first_row = ET.SubElement(style, f"{W}tblStylePr", {f"{W}type": "firstRow"})
            cell_properties = _child(first_row, "tcPr")
            _child(cell_properties, "shd").set(f"{W}fill", "E9F4F0")
            first_row_run = _child(first_row, "rPr")
            _child(first_row_run, "b")
            _child(first_row_run, "color").set(f"{W}val", "16483C")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _fallback_font(character: str) -> str | None:
    codepoint = ord(character)
    if codepoint >= 0x1F000:
        return EMOJI_FONT
    if 0x2190 <= codepoint <= 0x2BFF:
        return SYMBOL_FONT
    return None


def _split_symbol_runs(root: ET.Element) -> None:
    parents = {child: parent for parent in root.iter() for child in parent}
    for run in list(root.iter(f"{W}r")):
        texts = run.findall(f"{W}t")
        if len(texts) != 1 or len(run) > (2 if run.find(f"{W}rPr") is not None else 1):
            continue
        value = texts[0].text or ""
        if not value or not any(_fallback_font(character) for character in value):
            continue
        segments: list[tuple[str | None, str]] = []
        for character in value:
            font = _fallback_font(character)
            if segments and segments[-1][0] == font:
                segments[-1] = (font, segments[-1][1] + character)
            else:
                segments.append((font, character))
        parent = parents[run]
        position = list(parent).index(run)
        parent.remove(run)
        for offset, (font, text) in enumerate(segments):
            replacement = copy.deepcopy(run)
            replacement_text = replacement.find(f"{W}t")
            replacement_text.text = text
            replacement_text.set(f"{{{XML_NS}}}space", "preserve")
            if font:
                properties = replacement.find(f"{W}rPr")
                if properties is None:
                    properties = ET.Element(f"{W}rPr")
                    replacement.insert(0, properties)
                _set_font(properties, font)
            parent.insert(position + offset, replacement)


def _style_document(xml: bytes) -> bytes:
    root = ET.fromstring(xml)
    _allow_page_splitting(root)
    _split_symbol_runs(root)

    for background in root.findall(f"{W}background"):
        root.remove(background)
    background = ET.Element(f"{W}background", {f"{W}color": "FFFFFF"})
    root.insert(0, background)

    section = root.find(f".//{W}sectPr")
    if section is not None:
        page_size = _child(section, "pgSz")
        page_size.set(f"{W}w", "11906")
        page_size.set(f"{W}h", "16838")
        margins = _child(section, "pgMar")
        for edge in ("top", "right", "bottom", "left"):
            margins.set(f"{W}{edge}", "680")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def style_docx(path: Path) -> None:
    """Apply the PDF print system to an already generated DOCX package."""
    with zipfile.ZipFile(path, "r") as source:
        entries = [(item, source.read(item.filename)) for item in source.infolist()]

    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for item, content in entries:
                if item.filename == "word/styles.xml":
                    content = _style_styles(content)
                elif item.filename == "word/document.xml":
                    content = _style_document(content)
                target.writestr(item, content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
